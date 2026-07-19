using System.Text.Json;
using System.Text.Json.Serialization;

namespace AutoCADMcp.Plugin;

internal static class NativeProtocol
{
    internal const int Version = 1;
    internal const int CapabilityVersion = 1;
    internal const string PluginVersion = "4.0.0";

    internal static readonly string[] Capabilities =
    [
        "document.context",
        "document.create",
        "document.activate",
        "transaction.execute",
        "entity.line",
        "entity.circle",
        "solid.box",
        "solid.cylinder",
        "solid.boolean",
        "document.revision.database-events",
        "feature-id.xdata",
    ];
}

internal sealed record RpcRequest(
    string Id,
    string Operation,
    string? Token,
    string? SessionId,
    string? DocId,
    long? ExpectedRevision,
    string? IdempotencyKey,
    JsonElement Data);

internal sealed record RpcError(
    string Code,
    string Message,
    bool Recoverable = false,
    string? RecommendedAction = null,
    object? Details = null);

internal sealed record RpcResponse(
    string Id,
    bool Ok,
    object? Payload = null,
    RpcError? Error = null)
{
    internal static RpcResponse Success(string id, object? payload) => new(id, true, payload);

    internal static RpcResponse Failure(
        string id,
        string code,
        string message,
        bool recoverable = false,
        string? recommendedAction = null,
        object? details = null) =>
        new(id, false, null, new RpcError(code, message, recoverable, recommendedAction, details));
}

internal sealed class CadProtocolException : Exception
{
    internal string Code { get; }
    internal bool Recoverable { get; }
    internal string? RecommendedAction { get; }
    internal object? Details { get; }

    internal CadProtocolException(
        string code,
        string message,
        bool recoverable = false,
        string? recommendedAction = null,
        object? details = null) : base(message)
    {
        Code = code;
        Recoverable = recoverable;
        RecommendedAction = recommendedAction;
        Details = details;
    }
}

internal static class JsonProtocol
{
    internal static readonly JsonSerializerOptions Options = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
        PropertyNameCaseInsensitive = true,
        DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
    };
}
