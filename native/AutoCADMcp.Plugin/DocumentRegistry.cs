using Autodesk.AutoCAD.ApplicationServices;
using AcApplication = Autodesk.AutoCAD.ApplicationServices.Core.Application;

namespace AutoCADMcp.Plugin;

internal sealed class NativeDocumentState
{
    internal string DocId { get; } = $"acad-{Guid.NewGuid():N}";
    internal long Revision { get; set; }
    internal int SuppressEvents { get; set; }
}

internal sealed class DocumentRegistry
{
    private readonly object gate = new();
    private readonly Dictionary<Document, NativeDocumentState> documents = new();

    internal string SessionId { get; } = $"native-{Guid.NewGuid():N}";

    internal DocumentRegistry()
    {
        AcApplication.DocumentManager.DocumentCreated += (_, args) => Register(args.Document);
        AcApplication.DocumentManager.DocumentToBeDestroyed += (_, args) => Unregister(args.Document);
        foreach (Document document in AcApplication.DocumentManager)
        {
            Register(document);
        }
    }

    internal NativeDocumentState Register(Document document)
    {
        lock (gate)
        {
            if (documents.TryGetValue(document, out NativeDocumentState? current))
            {
                return current;
            }
            var state = new NativeDocumentState();
            documents[document] = state;
            document.Database.ObjectAppended += (_, _) => Touch(document);
            document.Database.ObjectModified += (_, _) => Touch(document);
            document.Database.ObjectErased += (_, _) => Touch(document);
            return state;
        }
    }

    internal Document Resolve(string? docId)
    {
        lock (gate)
        {
            if (docId is null)
            {
                return AcApplication.DocumentManager.MdiActiveDocument
                    ?? throw new CadProtocolException("E_NO_ACTIVE_DOCUMENT", "AutoCAD has no active document");
            }
            foreach ((Document document, NativeDocumentState state) in documents)
            {
                if (state.DocId == docId)
                {
                    return document;
                }
            }
        }
        throw new CadProtocolException("E_DOCUMENT_ID_MISMATCH", $"Unknown document id: {docId}");
    }

    internal NativeDocumentState State(Document document) => Register(document);

    private void Unregister(Document document)
    {
        lock (gate)
        {
            documents.Remove(document);
        }
    }

    internal void Validate(Document document, RpcRequest request)
    {
        NativeDocumentState state = State(document);
        if (request.SessionId is not null && request.SessionId != SessionId)
        {
            throw new CadProtocolException(
                "E_SESSION_GENERATION_MISMATCH",
                "The native AutoCAD session changed",
                recommendedAction: "read_document_context_and_retry");
        }
        if (request.DocId is not null && request.DocId != state.DocId)
        {
            throw new CadProtocolException("E_DOCUMENT_ID_MISMATCH", "The requested document is not active");
        }
        if (!ReferenceEquals(AcApplication.DocumentManager.MdiActiveDocument, document))
        {
            throw new CadProtocolException(
                "E_DOCUMENT_ID_MISMATCH",
                "The requested document is not the active AutoCAD document",
                recommendedAction: "activate_the_requested_document_and_retry",
                details: Context(document));
        }
        if (request.ExpectedRevision is null || request.ExpectedRevision.Value != state.Revision)
        {
            throw new CadProtocolException(
                "E_DOCUMENT_REVISION_MISMATCH",
                "The requested document revision is stale",
                details: Context(document));
        }
    }

    internal Dictionary<string, object?> Context(Document document)
    {
        NativeDocumentState state = State(document);
        string activePath = string.IsNullOrWhiteSpace(document.Database.Filename)
            ? document.Name
            : document.Database.Filename;
        return new Dictionary<string, object?>
        {
            ["sessionId"] = SessionId,
            ["workerGeneration"] = Environment.ProcessId,
            ["workerProcessId"] = Environment.ProcessId,
            ["docId"] = state.DocId,
            ["activeDocId"] = state.DocId,
            ["revision"] = state.Revision,
            ["requestedPath"] = string.IsNullOrWhiteSpace(activePath) ? null : activePath,
            ["activePath"] = string.IsNullOrWhiteSpace(activePath) ? null : activePath,
            ["documentName"] = document.Name,
            ["revisionSource"] = "native-database-events",
        };
    }

    internal void BeginMutation(Document document)
    {
        lock (gate)
        {
            State(document).SuppressEvents += 1;
        }
    }

    internal void EndMutation(Document document, bool committed)
    {
        lock (gate)
        {
            NativeDocumentState state = State(document);
            state.SuppressEvents = Math.Max(0, state.SuppressEvents - 1);
            if (committed)
            {
                state.Revision += 1;
            }
        }
    }

    private void Touch(Document document)
    {
        lock (gate)
        {
            NativeDocumentState state = State(document);
            if (state.SuppressEvents == 0)
            {
                state.Revision += 1;
            }
        }
    }
}
