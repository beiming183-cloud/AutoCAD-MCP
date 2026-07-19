using System.Text.Json;
using Autodesk.AutoCAD.Runtime;
using AcApplication = Autodesk.AutoCAD.ApplicationServices.Core.Application;

[assembly: ExtensionApplication(typeof(AutoCADMcp.Plugin.PluginEntry))]

namespace AutoCADMcp.Plugin;

public sealed class PluginEntry : IExtensionApplication
{
    private PipeServer? server;
    private string? descriptorPath;

    public void Initialize()
    {
        try
        {
            var registry = new DocumentRegistry();
            var dispatcher = new CadDispatcher(registry);
            string pipeName = Environment.GetEnvironmentVariable("AUTOCAD_MCP_PLUGIN_PIPE")
                ?? $"autocad-mcp-{Environment.ProcessId}";
            server = new PipeServer(pipeName, dispatcher);
            server.Start();
            descriptorPath = WriteDescriptor(pipeName, registry.SessionId);
        }
        catch (System.Exception error)
        {
            // An extension-load failure must not escape into AutoCAD's host
            // loader.  In particular, restricted AppData/profile ACLs can
            // reject descriptor creation; that should degrade the native
            // transport to unavailable while leaving the user's CAD session
            // alive for the compatibility backend.
            try
            {
                server?.Dispose();
            }
            catch
            {
                // Preserve the original initialization diagnostic.
            }
            server = null;
            descriptorPath = null;
            System.Diagnostics.Trace.WriteLine(
                $"AutoCAD-MCP native plugin initialization failed: {error}");
        }
    }

    public void Terminate()
    {
        try
        {
            server?.Dispose();
        }
        catch (System.Exception error)
        {
            System.Diagnostics.Trace.WriteLine(
                $"AutoCAD-MCP native plugin shutdown failed: {error}");
        }
        if (descriptorPath is not null)
        {
            try
            {
                File.Delete(descriptorPath);
            }
            catch
            {
            }
        }
    }

    private static string WriteDescriptor(string pipeName, string sessionId)
    {
        string root = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "AutoCAD-MCP",
            "workers");
        Directory.CreateDirectory(root);
        string destination = Path.Combine(root, $"{Environment.ProcessId}.json");
        string temporary = destination + ".tmp";
        File.WriteAllText(
            temporary,
            JsonSerializer.Serialize(
                new
                {
                    protocolVersion = NativeProtocol.Version,
                    capabilityVersion = NativeProtocol.CapabilityVersion,
                    pluginVersion = NativeProtocol.PluginVersion,
                    capabilities = NativeProtocol.Capabilities,
                    pipeName,
                    sessionId,
                    processId = Environment.ProcessId,
                    hwnd = AcApplication.MainWindow.Handle.ToInt64(),
                    startedAt = DateTimeOffset.UtcNow,
                },
                JsonProtocol.Options));
        File.Move(temporary, destination, true);
        return destination;
    }
}
