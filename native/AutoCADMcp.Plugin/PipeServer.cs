using System.Buffers.Binary;
using System.IO.Pipes;
using System.Text.Json;

namespace AutoCADMcp.Plugin;

internal sealed class PipeServer : IDisposable
{
    private const int MaximumMessageBytes = 8 * 1024 * 1024;
    private readonly string pipeName;
    private readonly CadDispatcher dispatcher;
    private readonly CancellationTokenSource cancellation = new();
    private Task? listener;

    internal PipeServer(string pipeName, CadDispatcher dispatcher)
    {
        this.pipeName = pipeName;
        this.dispatcher = dispatcher;
    }

    internal void Start() => listener = Task.Run(ListenAsync);

    private async Task ListenAsync()
    {
        while (!cancellation.IsCancellationRequested)
        {
            try
            {
                await using var pipe = new NamedPipeServerStream(
                    pipeName,
                    PipeDirection.InOut,
                    1,
                    PipeTransmissionMode.Byte,
                    PipeOptions.Asynchronous | PipeOptions.CurrentUserOnly);
                await pipe.WaitForConnectionAsync(cancellation.Token).ConfigureAwait(false);
                while (pipe.IsConnected && !cancellation.IsCancellationRequested)
                {
                    byte[]? message = await ReadFrameAsync(pipe, cancellation.Token).ConfigureAwait(false);
                    if (message is null)
                    {
                        break;
                    }
                    RpcResponse response;
                    try
                    {
                        RpcRequest request = JsonSerializer.Deserialize<RpcRequest>(
                            message, JsonProtocol.Options) ?? throw new JsonException("Request body is empty");
                        response = await dispatcher.DispatchAsync(request).ConfigureAwait(false);
                    }
                    catch (Exception error)
                    {
                        response = RpcResponse.Failure(
                            "unknown",
                            "E_PROTOCOL_ERROR",
                            error.Message,
                            details: new { exceptionType = error.GetType().FullName });
                    }
                    byte[] encoded = JsonSerializer.SerializeToUtf8Bytes(response, JsonProtocol.Options);
                    await WriteFrameAsync(pipe, encoded, cancellation.Token).ConfigureAwait(false);
                }
            }
            catch (OperationCanceledException) when (cancellation.IsCancellationRequested)
            {
                return;
            }
            catch
            {
                try
                {
                    await Task.Delay(250, cancellation.Token).ConfigureAwait(false);
                }
                catch (OperationCanceledException)
                {
                    return;
                }
            }
        }
    }

    private static async Task<byte[]?> ReadFrameAsync(Stream stream, CancellationToken token)
    {
        byte[] header = new byte[4];
        if (!await ReadExactAsync(stream, header, token).ConfigureAwait(false))
        {
            return null;
        }
        int length = BinaryPrimitives.ReadInt32LittleEndian(header);
        if (length <= 0 || length > MaximumMessageBytes)
        {
            throw new InvalidDataException($"Invalid message length: {length}");
        }
        byte[] body = new byte[length];
        return await ReadExactAsync(stream, body, token).ConfigureAwait(false) ? body : null;
    }

    private static async Task<bool> ReadExactAsync(Stream stream, byte[] buffer, CancellationToken token)
    {
        int offset = 0;
        while (offset < buffer.Length)
        {
            int read = await stream.ReadAsync(buffer.AsMemory(offset), token).ConfigureAwait(false);
            if (read == 0)
            {
                return false;
            }
            offset += read;
        }
        return true;
    }

    private static async Task WriteFrameAsync(Stream stream, byte[] body, CancellationToken token)
    {
        byte[] header = new byte[4];
        BinaryPrimitives.WriteInt32LittleEndian(header, body.Length);
        await stream.WriteAsync(header, token).ConfigureAwait(false);
        await stream.WriteAsync(body, token).ConfigureAwait(false);
        await stream.FlushAsync(token).ConfigureAwait(false);
    }

    public void Dispose()
    {
        cancellation.Cancel();
        try
        {
            listener?.Wait(TimeSpan.FromSeconds(2));
        }
        catch
        {
        }
        cancellation.Dispose();
    }
}
