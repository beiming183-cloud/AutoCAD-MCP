param(
    [string]$AutoCADDir = $env:AUTOCAD_MCP_AUTOCAD_DIR,
    [string]$DotNet = "dotnet",
    [string]$CertificateThumbprint,
    [string]$SignTool = "signtool.exe",
    [switch]$Install,
    [ValidateSet("Debug", "Release")]
    [string]$Configuration = "Release"
)

$ErrorActionPreference = "Stop"
$nativeRoot = Split-Path -Parent $PSScriptRoot
$project = Join-Path $nativeRoot "AutoCADMcp.Plugin\AutoCADMcp.Plugin.csproj"
$manifest = Join-Path $nativeRoot "AutoCADMcp.bundle\PackageContents.xml"
$artifactRoot = Join-Path $nativeRoot "artifacts\AutoCADMcp.bundle"
$contentRoot = Join-Path $artifactRoot "Contents\Windows"

if (-not $AutoCADDir -or -not (Test-Path -LiteralPath (Join-Path $AutoCADDir "AcMgd.dll"))) {
    throw "AutoCADDir must point to an AutoCAD 2025/2026 installation."
}

$env:AUTOCAD_MCP_AUTOCAD_DIR = $AutoCADDir
& $DotNet build $project -c $Configuration --nologo
if ($LASTEXITCODE -ne 0) {
    throw "Native plugin build failed with exit code $LASTEXITCODE."
}

New-Item -ItemType Directory -Force -Path $contentRoot | Out-Null
Copy-Item -LiteralPath $manifest -Destination (Join-Path $artifactRoot "PackageContents.xml") -Force
$assembly = Join-Path $nativeRoot "AutoCADMcp.Plugin\bin\$Configuration\net8.0-windows\AutoCADMcp.Plugin.dll"
$packagedAssembly = Join-Path $contentRoot "AutoCADMcp.Plugin.dll"
Copy-Item -LiteralPath $assembly -Destination $packagedAssembly -Force

if ($CertificateThumbprint) {
    & $SignTool sign /sha1 $CertificateThumbprint /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 $packagedAssembly
    if ($LASTEXITCODE -ne 0) {
        throw "Authenticode signing failed with exit code $LASTEXITCODE."
    }
    & $SignTool verify /pa $packagedAssembly
    if ($LASTEXITCODE -ne 0) {
        throw "The packaged native plugin did not pass Authenticode verification."
    }
}

$installedBundle = $null
if ($Install) {
    if (-not $CertificateThumbprint) {
        throw "-Install requires -CertificateThumbprint; SECURELOAD must remain enabled."
    }
    $applicationPlugins = Join-Path $env:APPDATA "Autodesk\ApplicationPlugins"
    $installedBundle = Join-Path $applicationPlugins "AutoCADMcp.bundle"
    New-Item -ItemType Directory -Force -Path $applicationPlugins | Out-Null
    $resolvedPlugins = [System.IO.Path]::GetFullPath($applicationPlugins).TrimEnd('\')
    $resolvedBundle = [System.IO.Path]::GetFullPath($installedBundle)
    if (-not $resolvedBundle.StartsWith($resolvedPlugins + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to replace a bundle outside the Autodesk ApplicationPlugins directory: $resolvedBundle"
    }
    if (Test-Path -LiteralPath $installedBundle) {
        Remove-Item -LiteralPath $installedBundle -Recurse -Force
    }
    Copy-Item -LiteralPath $artifactRoot -Destination $installedBundle -Recurse -Force
}

[pscustomobject]@{
    Bundle = $artifactRoot
    Assembly = $packagedAssembly
    Signed = [bool]$CertificateThumbprint
    InstalledBundle = $installedBundle
    AutoCADDir = (Resolve-Path -LiteralPath $AutoCADDir).Path
    Configuration = $Configuration
}
