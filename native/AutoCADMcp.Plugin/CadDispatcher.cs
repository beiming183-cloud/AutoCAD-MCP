using System.Collections.Concurrent;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using Autodesk.AutoCAD.ApplicationServices;
using Autodesk.AutoCAD.DatabaseServices;
using Autodesk.AutoCAD.Geometry;
using AcApplication = Autodesk.AutoCAD.ApplicationServices.Core.Application;

namespace AutoCADMcp.Plugin;

internal sealed class CadDispatcher
{
    private const string XDataApplication = "AUTOCAD_MCP";
    private readonly DocumentRegistry registry;
    private readonly string? requiredToken;
    private readonly ConcurrentDictionary<string, (string Hash, RpcResponse Response)> completed = new();

    internal CadDispatcher(DocumentRegistry registry)
    {
        this.registry = registry;
        requiredToken = Environment.GetEnvironmentVariable("AUTOCAD_MCP_PLUGIN_TOKEN");
    }

    internal Task<RpcResponse> DispatchAsync(RpcRequest request)
    {
        if (!TokenMatches(request.Token))
        {
            return Task.FromResult(RpcResponse.Failure(
                request.Id, "E_PLUGIN_AUTHENTICATION", "The native plugin token is invalid"));
        }
        if (request.Operation == "document.create" && string.IsNullOrWhiteSpace(request.IdempotencyKey))
        {
            return Task.FromResult(RpcResponse.Failure(
                request.Id,
                "E_PARAMETER_REJECTED",
                "document.create requires idempotencyKey"));
        }

        string requestHash = RequestHash(request);
        if (request.IdempotencyKey is not null && completed.TryGetValue(request.IdempotencyKey, out var prior))
        {
            return Task.FromResult(
                prior.Hash == requestHash
                    ? ReplayResponse(request.Id, prior.Response)
                    : RpcResponse.Failure(
                        request.Id,
                        "E_IDEMPOTENCY_CONFLICT",
                        "The idempotency key was used for a different native request"));
        }

        var completion = new TaskCompletionSource<RpcResponse>(TaskCreationOptions.RunContinuationsAsynchronously);
        try
        {
            AcApplication.DocumentManager.ExecuteInApplicationContext(_ =>
            {
                RpcResponse response;
                try
                {
                    response = DispatchOnAutoCADThread(request);
                }
                catch (CadProtocolException error)
                {
                    response = RpcResponse.Failure(
                        request.Id,
                        error.Code,
                        error.Message,
                        error.Recoverable,
                        error.RecommendedAction,
                        error.Details);
                }
                catch (Exception error)
                {
                    response = RpcResponse.Failure(
                        request.Id,
                        "E_NATIVE_PLUGIN_FAILURE",
                        error.Message,
                        details: new { exceptionType = error.GetType().FullName });
                }

                // A failed mutation was never committed, so it must remain retryable.
                // Only successful responses are safe to replay indefinitely.
                if (request.IdempotencyKey is not null && response.Ok)
                {
                    completed.TryAdd(request.IdempotencyKey, (requestHash, response));
                }
                completion.TrySetResult(response);
            }, null);
        }
        catch (Exception error)
        {
            // ExecuteInApplicationContext can reject work synchronously while
            // AutoCAD is closing or inside a modal command.  Complete the RPC
            // immediately; otherwise the pipe client only sees a timeout and
            // may retry an ambiguous mutation.
            completion.TrySetResult(RpcResponse.Failure(
                request.Id,
                "E_AUTOCAD_CONTEXT_UNAVAILABLE",
                "AutoCAD rejected scheduling the native operation",
                recoverable: true,
                recommendedAction: "wait_for_autocad_to_return_to_idle_then_retry",
                details: new { exceptionType = error.GetType().FullName, error = error.Message }));
        }
        return completion.Task;
    }

    private static RpcResponse ReplayResponse(string requestId, RpcResponse prior)
    {
        if (prior.Payload is Dictionary<string, object?> dictionary)
        {
            var payload = new Dictionary<string, object?>(dictionary)
            {
                ["idempotentReplay"] = true,
            };
            return RpcResponse.Success(requestId, payload);
        }
        return RpcResponse.Success(
            requestId,
            new { idempotentReplay = true, result = prior.Payload });
    }

    private RpcResponse DispatchOnAutoCADThread(RpcRequest request) => request.Operation switch
    {
        "system.ping" => RpcResponse.Success(request.Id, new
        {
            pong = true,
            protocolVersion = NativeProtocol.Version,
            capabilityVersion = NativeProtocol.CapabilityVersion,
            pluginVersion = NativeProtocol.PluginVersion,
            capabilities = NativeProtocol.Capabilities,
            sessionId = registry.SessionId,
            processId = Environment.ProcessId,
        }),
        "document.context" => DocumentContext(request),
        "document.create" => CreateDocument(request),
        "document.activate" => ActivateDocument(request),
        "transaction.execute" => ExecuteTransaction(request),
        _ => RpcResponse.Failure(
            request.Id,
            "E_UNSUPPORTED_OPERATION",
            $"Unsupported native operation: {request.Operation}"),
    };

    private RpcResponse DocumentContext(RpcRequest request)
    {
        Document document = registry.Resolve(request.DocId);
        return RpcResponse.Success(request.Id, registry.Context(document));
    }

    private RpcResponse CreateDocument(RpcRequest request)
    {
        if (request.SessionId is not null && request.SessionId != registry.SessionId)
        {
            return RpcResponse.Failure(
                request.Id,
                "E_SESSION_GENERATION_MISMATCH",
                "The native AutoCAD session changed",
                recoverable: true,
                recommendedAction: "rediscover_the_native_worker_and_read_document_context");
        }
        string template = request.Data.TryGetProperty("template", out JsonElement value)
            ? value.GetString() ?? "acadiso.dwt"
            : "acadiso.dwt";
        string? requestedPath = request.Data.TryGetProperty("path", out JsonElement pathValue)
            ? pathValue.GetString()
            : null;
        Document? previous = AcApplication.DocumentManager.MdiActiveDocument;
        Document? document = null;
        try
        {
            document = DocumentCollectionExtension.Add(AcApplication.DocumentManager, template);
            AcApplication.DocumentManager.MdiActiveDocument = document;
            string? fullPath = null;
            if (!string.IsNullOrWhiteSpace(requestedPath))
            {
                fullPath = Path.GetFullPath(requestedPath);
                Directory.CreateDirectory(Path.GetDirectoryName(fullPath)!);
                using DocumentLock documentLock = document.LockDocument();
                document.Database.SaveAs(fullPath, DwgVersion.Current);
            }
            Dictionary<string, object?> context = registry.Context(document);
            context["requestedPath"] = requestedPath;
            object[] diff = string.IsNullOrWhiteSpace(fullPath)
                ? Array.Empty<object>()
                : fullPath.Equals(document.Database.Filename, StringComparison.OrdinalIgnoreCase)
                    ? Array.Empty<object>()
                    : new object[]
                    {
                        new
                        {
                            path = "activePath",
                            requested = fullPath,
                            actual = document.Database.Filename,
                        },
                    };
            context["diff"] = diff;
            if (diff.Length > 0)
            {
                document.CloseAndDiscard();
                if (previous is not null && !previous.IsDisposed)
                {
                    AcApplication.DocumentManager.MdiActiveDocument = previous;
                }
                return RpcResponse.Failure(
                    request.Id,
                    "E_POSTCONDITION_MISMATCH",
                    "AutoCAD created a document at a path different from the requested path",
                    recoverable: false,
                    recommendedAction: "inspect_the_template_and_output_path_before_retrying",
                    details: context);
            }
            return RpcResponse.Success(request.Id, context);
        }
        catch (Exception exception)
        {
            if (document is not null)
            {
                try
                {
                    document.CloseAndDiscard();
                }
                catch
                {
                    // The structured error below records cleanup failure; do
                    // not let a second AutoCAD exception mask the root cause.
                }
            }
            try
            {
                if (previous is not null && !previous.IsDisposed)
                {
                    AcApplication.DocumentManager.MdiActiveDocument = previous;
                }
            }
            catch
            {
                // Preserve the original failure and let the caller reconcile
                // the active document through document.context.
            }
            return RpcResponse.Failure(
                request.Id,
                "E_DOCUMENT_CREATE_FAILED",
                $"AutoCAD could not create the requested document: {exception.Message}",
                recoverable: true,
                recommendedAction: "read_document_context_and_verify_that_no_extra_document_remains",
                details: new { requestedPath, cleanupAttempted = document is not null });
        }
    }

    private RpcResponse ActivateDocument(RpcRequest request)
    {
        if (request.SessionId is not null && request.SessionId != registry.SessionId)
        {
            return RpcResponse.Failure(
                request.Id,
                "E_SESSION_GENERATION_MISMATCH",
                "The native AutoCAD session changed",
                recoverable: true,
                recommendedAction: "rediscover_the_native_worker_and_read_document_context");
        }
        if (string.IsNullOrWhiteSpace(request.DocId) ||
            request.ExpectedRevision is null ||
            request.ExpectedRevision.Value < 0)
        {
            return RpcResponse.Failure(
                request.Id,
                "E_PARAMETER_REJECTED",
                "document.activate requires docId and a non-negative expectedRevision",
                recoverable: false,
                recommendedAction: "read_document_context_and_retry",
                details: new { required = new[] { "docId", "expectedRevision" } });
        }
        Document document = registry.Resolve(request.DocId);
        NativeDocumentState state = registry.State(document);
        if (request.ExpectedRevision.Value != state.Revision)
        {
            return RpcResponse.Failure(
                request.Id,
                "E_DOCUMENT_REVISION_MISMATCH",
                "The requested document revision is stale",
                recoverable: false,
                recommendedAction: "read_latest_document_revision_and_retry",
                details: new
                {
                    requestedDocId = request.DocId,
                    expectedRevision = request.ExpectedRevision,
                    actual = registry.Context(document),
                });
        }
        AcApplication.DocumentManager.MdiActiveDocument = document;
        Document? active = AcApplication.DocumentManager.MdiActiveDocument;
        if (!ReferenceEquals(active, document))
        {
            return RpcResponse.Failure(
                request.Id,
                "E_DOCUMENT_ID_MISMATCH",
                "AutoCAD did not activate the requested document",
                recoverable: true,
                recommendedAction: "read_document_context_and_retry",
                details: new
                {
                    requestedDocId = request.DocId,
                    expectedRevision = request.ExpectedRevision,
                    requested = registry.Context(document),
                    actual = active is null ? null : registry.Context(active),
                });
        }
        Dictionary<string, object?> payload = registry.Context(document);
        payload["requestedDocId"] = request.DocId;
        payload["expectedRevision"] = request.ExpectedRevision;
        payload["requested"] = registry.Context(document);
        payload["actual"] = registry.Context(document);
        payload["diff"] = Array.Empty<object>();
        return RpcResponse.Success(request.Id, payload);
    }

    private RpcResponse ExecuteTransaction(RpcRequest request)
    {
        if (string.IsNullOrWhiteSpace(request.IdempotencyKey))
        {
            throw new CadProtocolException(
                "E_PARAMETER_REJECTED", "transaction.execute requires idempotencyKey");
        }
        Document document = registry.Resolve(request.DocId);
        registry.Validate(document, request);
        JsonElement operations = request.Data.GetProperty("operations");
        if (operations.ValueKind != JsonValueKind.Array || operations.GetArrayLength() == 0)
        {
            throw new CadProtocolException("E_PARAMETER_REJECTED", "operations must be a non-empty array");
        }

        var results = new List<object>();
        var references = new Dictionary<string, Entity>(StringComparer.Ordinal);
        bool committed = false;
        registry.BeginMutation(document);
        try
        {
            using DocumentLock documentLock = document.LockDocument();
            using Transaction transaction = document.Database.TransactionManager.StartTransaction();
            BlockTable blockTable = (BlockTable)transaction.GetObject(
                document.Database.BlockTableId, OpenMode.ForRead);
            BlockTableRecord modelSpace = (BlockTableRecord)transaction.GetObject(
                blockTable[BlockTableRecord.ModelSpace], OpenMode.ForWrite);
            EnsureXDataApplication(transaction, document.Database);

            int index = 0;
            foreach (JsonElement operation in operations.EnumerateArray())
            {
                results.Add(ExecuteOperation(
                    transaction,
                    document.Database,
                    modelSpace,
                    references,
                    operation,
                    index));
                index += 1;
            }
            transaction.Commit();
            committed = true;
        }
        finally
        {
            registry.EndMutation(document, committed);
        }

        Dictionary<string, object?> context = registry.Context(document);
        context["transactionState"] = "committed";
        context["results"] = results;
        context["operationCount"] = results.Count;
        context["idempotencyKey"] = request.IdempotencyKey;
        return RpcResponse.Success(request.Id, context);
    }

    private object ExecuteOperation(
        Transaction transaction,
        Database database,
        BlockTableRecord modelSpace,
        Dictionary<string, Entity> references,
        JsonElement operation,
        int index)
    {
        string kind = RequiredString(operation, "type");
        ValidateOperationFields(kind, operation);
        string resultId = operation.TryGetProperty("resultId", out JsonElement resultIdValue)
            ? resultIdValue.GetString() ?? $"result-{index}"
            : $"result-{index}";
        string layer = operation.TryGetProperty("layer", out JsonElement layerValue)
            ? layerValue.GetString() ?? "0"
            : "0";
        RequireLayer(transaction, database, layer);

        if (kind == "solid.boolean")
        {
            Solid3d primary = RequireSolid(references, RequiredString(operation, "primaryRef"));
            Solid3d tool = RequireSolid(references, RequiredString(operation, "toolRef"));
            string booleanKind = RequiredString(operation, "operation");
            BooleanOperationType operationType = booleanKind switch
            {
                "union" => BooleanOperationType.BoolUnite,
                "intersection" => BooleanOperationType.BoolIntersect,
                "subtract" => BooleanOperationType.BoolSubtract,
                _ => throw new CadProtocolException(
                    "E_PARAMETER_REJECTED", $"Unknown boolean operation: {booleanKind}"),
            };
            primary.BooleanOperation(operationType, tool);
            string featureId = FeatureId(operation);
            SetFeatureIdentity(primary, featureId);
            references[resultId] = primary;
            return VerifiedEntityResult(resultId, primary, kind, featureId, operation);
        }

        Entity entity = kind switch
        {
            "entity.line" => new Line(Point(operation, "start"), Point(operation, "end")),
            "entity.circle" => new Circle(
                Point(operation, "center"), Vector3d.ZAxis, Positive(operation, "radius")),
            "solid.box" => CreateBox(operation),
            "solid.cylinder" => CreateCylinder(operation),
            _ => throw new CadProtocolException(
                "E_UNSUPPORTED_OPERATION", $"Unsupported transaction item: {kind}"),
        };
        entity.Layer = layer;
        modelSpace.AppendEntity(entity);
        transaction.AddNewlyCreatedDBObject(entity, true);
        string createdFeatureId = FeatureId(operation);
        SetFeatureIdentity(entity, createdFeatureId);
        references[resultId] = entity;
        return VerifiedEntityResult(resultId, entity, kind, createdFeatureId, operation);
    }

    private static Solid3d CreateBox(JsonElement data)
    {
        Point3d center = Point(data, "center");
        double length = Positive(data, "length");
        double width = Positive(data, "width");
        double height = Positive(data, "height");
        var solid = new Solid3d();
        solid.CreateBox(length, width, height);
        solid.TransformBy(Matrix3d.Displacement(new Vector3d(
            center.X - length / 2.0,
            center.Y - width / 2.0,
            center.Z - height / 2.0)));
        return solid;
    }

    private static void ValidateOperationFields(string kind, JsonElement operation)
    {
        string[] common =
        [
            "type",
            "resultId",
            "layer",
            "featureId",
            "componentId",
            "designRole",
            "viewId",
            "intentionalOpenEnd",
            "permittedCrossing",
            "sourceAuthority",
        ];
        string[] specific = kind switch
        {
            "entity.line" => ["start", "end"],
            "entity.circle" => ["center", "radius"],
            "solid.box" => ["center", "length", "width", "height"],
            "solid.cylinder" => ["baseCenter", "radius", "height"],
            "solid.boolean" => ["primaryRef", "toolRef", "operation"],
            _ => throw new CadProtocolException(
                "E_UNSUPPORTED_OPERATION", $"Unsupported transaction item: {kind}"),
        };
        HashSet<string> allowed = new(common.Concat(specific), StringComparer.Ordinal);
        string[] unknown = operation.EnumerateObject()
            .Select(property => property.Name)
            .Where(name => !allowed.Contains(name))
            .OrderBy(name => name, StringComparer.Ordinal)
            .ToArray();
        if (unknown.Length > 0)
        {
            throw new CadProtocolException(
                "E_PARAMETER_REJECTED",
                $"{kind} contains unsupported fields: {string.Join(", ", unknown)}",
                details: new { kind, unknown, allowed = allowed.OrderBy(name => name).ToArray() });
        }
    }

    private static Solid3d CreateCylinder(JsonElement data)
    {
        Point3d baseCenter = Point(data, "baseCenter");
        double radius = Positive(data, "radius");
        double height = RequiredDouble(data, "height");
        if (Math.Abs(height) <= 1e-9)
        {
            throw new CadProtocolException("E_PARAMETER_REJECTED", "height must be non-zero");
        }
        var solid = new Solid3d();
        solid.CreateFrustum(Math.Abs(height), radius, radius, radius);
        double z = height > 0 ? baseCenter.Z : baseCenter.Z + height;
        solid.TransformBy(Matrix3d.Displacement(new Vector3d(baseCenter.X, baseCenter.Y, z)));
        return solid;
    }

    private static object VerifiedEntityResult(
        string resultId,
        Entity entity,
        string kind,
        string featureId,
        JsonElement requested)
    {
        object[] diff = EntityDiff(entity, kind, requested);
        if (diff.Length > 0)
        {
            throw new CadProtocolException(
                "E_POSTCONDITION_MISMATCH",
                $"Native entity readback did not match the requested {kind}",
                recoverable: false,
                recommendedAction: "inspect_the_transaction_diff_and_retry_only_after_reconciling_the_document",
                details: new { resultId, kind, featureId, diff });
        }
        return EntityResult(resultId, entity, kind, featureId, requested, diff);
    }

    private static object[] EntityDiff(Entity entity, string kind, JsonElement requested)
    {
        var diff = new List<object>();
        const double tolerance = 1e-6;

        static double[] Point(JsonElement value)
        {
            if (value.ValueKind != JsonValueKind.Array || value.GetArrayLength() < 3)
            {
                throw new CadProtocolException("E_POSTCONDITION_MISMATCH", "A point readback request is malformed");
            }
            return value.EnumerateArray().Take(3).Select(item => item.GetDouble()).ToArray();
        }

        void Compare(string path, double expected, double actual)
        {
            if (Math.Abs(expected - actual) > tolerance)
            {
                diff.Add(new { path, requested = expected, actual, delta = actual - expected });
            }
        }

        void ComparePoint(string path, double[] expected, Point3d actual)
        {
            Compare($"{path}.x", expected[0], actual.X);
            Compare($"{path}.y", expected[1], actual.Y);
            Compare($"{path}.z", expected[2], actual.Z);
        }

        if (requested.TryGetProperty("layer", out JsonElement requestedLayer)
            && requestedLayer.ValueKind == JsonValueKind.String)
        {
            string expectedLayer = requestedLayer.GetString() ?? "0";
            if (!string.Equals(expectedLayer, entity.Layer, StringComparison.OrdinalIgnoreCase))
            {
                diff.Add(new { path = "layer", requested = expectedLayer, actual = entity.Layer });
            }
        }

        try
        {
            if (entity is Line line && requested.TryGetProperty("start", out JsonElement start)
                && requested.TryGetProperty("end", out JsonElement end))
            {
                ComparePoint("start", Point(start), line.StartPoint);
                ComparePoint("end", Point(end), line.EndPoint);
            }
            else if (entity is Circle circle && requested.TryGetProperty("center", out JsonElement center)
                && requested.TryGetProperty("radius", out JsonElement radius))
            {
                ComparePoint("center", Point(center), circle.Center);
                Compare("radius", radius.GetDouble(), circle.Radius);
            }
            else if (entity is Solid3d solid
                && (kind is "solid.box" or "solid.cylinder" or "solid.boolean"))
            {
                Extents3d extents = solid.GeometricExtents;
                if (kind == "solid.box" && requested.TryGetProperty("center", out JsonElement boxCenter))
                {
                    double length = requested.GetProperty("length").GetDouble();
                    double width = requested.GetProperty("width").GetDouble();
                    double height = requested.GetProperty("height").GetDouble();
                    double[] point = Point(boxCenter);
                    Compare("bounds.min.x", point[0] - length / 2.0, extents.MinPoint.X);
                    Compare("bounds.min.y", point[1] - width / 2.0, extents.MinPoint.Y);
                    Compare("bounds.min.z", point[2] - height / 2.0, extents.MinPoint.Z);
                    Compare("bounds.max.x", point[0] + length / 2.0, extents.MaxPoint.X);
                    Compare("bounds.max.y", point[1] + width / 2.0, extents.MaxPoint.Y);
                    Compare("bounds.max.z", point[2] + height / 2.0, extents.MaxPoint.Z);
                }
                else if (kind == "solid.cylinder"
                    && requested.TryGetProperty("baseCenter", out JsonElement baseCenter)
                    && requested.TryGetProperty("radius", out JsonElement cylinderRadius)
                    && requested.TryGetProperty("height", out JsonElement cylinderHeight))
                {
                    double[] point = Point(baseCenter);
                    double cylinderRadiusValue = cylinderRadius.GetDouble();
                    double height = cylinderHeight.GetDouble();
                    double minZ = height >= 0 ? point[2] : point[2] + height;
                    double maxZ = height >= 0 ? point[2] + height : point[2];
                    Compare("bounds.min.x", point[0] - cylinderRadiusValue, extents.MinPoint.X);
                    Compare("bounds.min.y", point[1] - cylinderRadiusValue, extents.MinPoint.Y);
                    Compare("bounds.min.z", minZ, extents.MinPoint.Z);
                    Compare("bounds.max.x", point[0] + cylinderRadiusValue, extents.MaxPoint.X);
                    Compare("bounds.max.y", point[1] + cylinderRadiusValue, extents.MaxPoint.Y);
                    Compare("bounds.max.z", maxZ, extents.MaxPoint.Z);
                    Compare("volume", Math.PI * cylinderRadiusValue * cylinderRadiusValue * Math.Abs(height), solid.MassProperties.Volume);
                }
                if (solid.MassProperties.Volume <= tolerance)
                {
                    diff.Add(new { path = "volume", requested = ">0", actual = solid.MassProperties.Volume });
                }
            }
        }
        catch (CadProtocolException)
        {
            throw;
        }
        catch (Exception exception)
        {
            diff.Add(new { path = "readback", requested = "readable", actual = exception.Message });
        }
        return diff.ToArray();
    }

    private static object EntityResult(
        string resultId,
        Entity entity,
        string kind,
        string featureId,
        JsonElement requested,
        object[] diff)
    {
        Extents3d extents = entity.GeometricExtents;
        double? volume = entity is Solid3d solid ? solid.MassProperties.Volume : null;
        return new
        {
            resultId,
            type = kind,
            handle = entity.Handle.ToString(),
            featureId,
            layer = entity.Layer,
            bounds = new
            {
                min = new[] { extents.MinPoint.X, extents.MinPoint.Y, extents.MinPoint.Z },
                max = new[] { extents.MaxPoint.X, extents.MaxPoint.Y, extents.MaxPoint.Z },
            },
            volume,
            requested,
            diff,
        };
    }

    private static void RequireLayer(Transaction transaction, Database database, string layer)
    {
        LayerTable table = (LayerTable)transaction.GetObject(database.LayerTableId, OpenMode.ForRead);
        if (!table.Has(layer))
        {
            throw new CadProtocolException(
                "E_LAYER_NOT_FOUND",
                $"Layer does not exist: {layer}",
                details: new { layer, entityCreated = false });
        }
    }

    private static void EnsureXDataApplication(Transaction transaction, Database database)
    {
        RegAppTable table = (RegAppTable)transaction.GetObject(database.RegAppTableId, OpenMode.ForRead);
        if (table.Has(XDataApplication))
        {
            return;
        }
        table.UpgradeOpen();
        using var record = new RegAppTableRecord { Name = XDataApplication };
        table.Add(record);
        transaction.AddNewlyCreatedDBObject(record, true);
    }

    private static void SetFeatureIdentity(Entity entity, string featureId)
    {
        entity.XData = new ResultBuffer(
            new TypedValue((int)DxfCode.ExtendedDataRegAppName, XDataApplication),
            new TypedValue((int)DxfCode.ExtendedDataAsciiString, featureId));
    }

    private static string FeatureId(JsonElement operation) =>
        operation.TryGetProperty("featureId", out JsonElement value)
            ? value.GetString() ?? $"feature-{Guid.NewGuid():N}"
            : $"feature-{Guid.NewGuid():N}";

    private static Solid3d RequireSolid(Dictionary<string, Entity> references, string key)
    {
        if (!references.TryGetValue(key, out Entity? entity) || entity is not Solid3d solid)
        {
            throw new CadProtocolException(
                "E_PARAMETER_REJECTED", $"Unknown solid reference: {key}");
        }
        return solid;
    }

    private static Point3d Point(JsonElement data, string name)
    {
        JsonElement value = data.GetProperty(name);
        if (value.ValueKind != JsonValueKind.Array || value.GetArrayLength() is < 2 or > 3)
        {
            throw new CadProtocolException(
                "E_PARAMETER_REJECTED", $"{name} must contain two or three coordinates");
        }
        double[] coordinates = value.EnumerateArray().Select(item => item.GetDouble()).ToArray();
        return new Point3d(
            coordinates[0], coordinates[1], coordinates.Length > 2 ? coordinates[2] : 0.0);
    }

    private static string RequiredString(JsonElement data, string name) =>
        data.TryGetProperty(name, out JsonElement value) && !string.IsNullOrWhiteSpace(value.GetString())
            ? value.GetString()!
            : throw new CadProtocolException("E_PARAMETER_REJECTED", $"{name} is required");

    private static double RequiredDouble(JsonElement data, string name) =>
        data.TryGetProperty(name, out JsonElement value) && value.TryGetDouble(out double number)
            ? number
            : throw new CadProtocolException("E_PARAMETER_REJECTED", $"{name} must be numeric");

    private static double Positive(JsonElement data, string name)
    {
        double value = RequiredDouble(data, name);
        if (value <= 0)
        {
            throw new CadProtocolException("E_PARAMETER_REJECTED", $"{name} must be positive");
        }
        return value;
    }

    private bool TokenMatches(string? supplied)
    {
        if (string.IsNullOrEmpty(requiredToken))
        {
            return true;
        }
        if (supplied is null)
        {
            return false;
        }
        return CryptographicOperations.FixedTimeEquals(
            Encoding.UTF8.GetBytes(requiredToken), Encoding.UTF8.GetBytes(supplied));
    }

    private static string RequestHash(RpcRequest request)
    {
        // Transport request ids and credentials may change during a safe retry;
        // the semantic mutation identity must not.
        byte[] encoded = JsonSerializer.SerializeToUtf8Bytes(
            new
            {
                request.Operation,
                request.SessionId,
                request.DocId,
                request.ExpectedRevision,
                request.IdempotencyKey,
                request.Data,
            },
            JsonProtocol.Options);
        return Convert.ToHexString(SHA256.HashData(encoded)).ToLowerInvariant();
    }
}
