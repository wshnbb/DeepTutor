param(
    [Parameter(Mandatory = $true)][string]$DesktopInstall,
    [string]$StateDirectory = (Join-Path $env:LOCALAPPDATA 'io.github.wshnbb.deeptutor'),
    [string]$SearchOrigin = ''
)
$ErrorActionPreference = 'Stop'
$name = 'bilibili-lessons'
$pythonExe = Join-Path $DesktopInstall 'runtime\python\python.exe'
if (-not (Test-Path -LiteralPath $pythonExe -PathType Leaf)) { throw 'Select an existing DeepTutor Desktop installation.' }
$state = (Resolve-Path -LiteralPath $StateDirectory).Path
$preferences = Get-Content -LiteralPath (Join-Path $state 'desktop.json') -Raw -Encoding utf8 | ConvertFrom-Json
$runtimeHome = [string]$preferences.home
if (-not [IO.Path]::IsPathFullyQualified($runtimeHome)) { throw 'Desktop data home must be an absolute path.' }
$readyFile = Get-ChildItem -LiteralPath (Join-Path $state 'control') -Filter '*.json' | Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not $readyFile) { throw 'Start DeepTutor Desktop before installing this extension.' }
$ready = Get-Content -LiteralPath $readyFile.FullName -Raw -Encoding utf8 | ConvertFrom-Json
if ($ready.instance -ne $readyFile.BaseName) { throw 'Desktop service instance mismatch.' }

function Assert-LoopbackOrigin([string]$Value) {
    $uri = [Uri]$Value
    if ($uri.Scheme -ne 'http' -or $uri.Host -ne '127.0.0.1' -or $uri.IsDefaultPort -or $uri.UserInfo -or $uri.AbsolutePath -ne '/' -or $uri.Query -or $uri.Fragment) {
        throw 'Expected an explicit HTTP loopback origin with a port.'
    }
    return $uri.GetLeftPart([UriPartial]::Authority)
}
$api = Assert-LoopbackOrigin ([string]$ready.backend_url)
if ($SearchOrigin) { $SearchOrigin = Assert-LoopbackOrigin $SearchOrigin }
$auth = Invoke-RestMethod -Uri "$api/api/auth/status" -TimeoutSec 10
if ($auth.enabled -ne $false -or $auth.user_id -ne 'local-admin' -or -not $auth.authenticated) {
    throw 'This extension supports the local single-user desktop account only.'
}
$existing = Invoke-RestMethod -Uri "$api/api/settings/mcp" -TimeoutSec 20
$skills = Invoke-RestMethod -Uri "$api/api/skills/list" -TimeoutSec 15
if ($existing.servers.PSObject.Properties.Name -contains $name -or @($skills.skills | Where-Object name -eq $name).Count) {
    throw 'A server or skill with this name exists; refusing to overwrite it.'
}
$installDir = Join-Path $runtimeHome 'integrations\bilibili-lessons'
if (Test-Path -LiteralPath $installDir) { throw "Extension folder already exists: $installDir" }
New-Item -ItemType Directory -Path $installDir -Force | Out-Null
foreach ($file in @('server.py', 'lesson_tools.py', 'SKILL.md', 'README.md')) {
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot $file) -Destination (Join-Path $installDir $file)
}
$childEnv = @{
    PYTHONUTF8 = '1'; PYTHONIOENCODING = 'utf-8'
    DEEPTUTOR_HOME = $runtimeHome; DEEPTUTOR_DESKTOP_STATE_DIR = $state
}
if ($SearchOrigin) { $childEnv.DEEPTUTOR_LESSON_SEARCH_ORIGIN = $SearchOrigin }
$config = @{
    type = 'stdio'; command = $pythonExe
    args = @('-X', 'utf8', (Join-Path $installDir 'server.py'))
    env = $childEnv; cwd = $installDir; tool_timeout = 300
    enabled_tools = @('*'); disabled_tools = @(); enabled = $true
}
$body = $config | ConvertTo-Json -Depth 5
$probe = Invoke-RestMethod -Uri "$api/api/settings/mcp/test" -Method Post -ContentType 'application/json' -Body $body -TimeoutSec 35
if (-not $probe.ok -or $probe.tools.Count -ne 2) { throw 'MCP discovery failed; no server registered.' }
$registered = Invoke-RestMethod -Uri "$api/api/settings/mcp/servers/$name" -Method Put -ContentType 'application/json' -Body $body -TimeoutSec 40
foreach ($entry in $existing.servers.PSObject.Properties) {
    if (($entry.Value | ConvertTo-Json -Depth 20 -Compress) -ne ($registered.servers.($entry.Name) | ConvertTo-Json -Depth 20 -Compress)) {
        throw "An existing MCP entry changed unexpectedly: $($entry.Name)"
    }
}
$skillPayload = @{
    name = $name
    description = '仅在用户明确要求找课、搜索或推荐B站视频/教程时使用。普通知识问答不触发搜索。'
    content = Get-Content -LiteralPath (Join-Path $installDir 'SKILL.md') -Raw -Encoding utf8
    tags = @()
}
try {
    $skillJson = [Text.Encoding]::UTF8.GetBytes(($skillPayload | ConvertTo-Json -Depth 5))
    $null = Invoke-RestMethod -Uri "$api/api/skills/create" -Method Post -ContentType 'application/json; charset=utf-8' -Body $skillJson -TimeoutSec 20
} catch {
    # Only this just-created registration is rolled back; other servers remain.
    $null = Invoke-RestMethod -Uri "$api/api/settings/mcp/servers/$name" -Method Delete -TimeoutSec 20
    throw
}
Write-Output "Installed $name at $installDir. Existing model settings were not changed."
$registered.status | Where-Object name -eq $name | Select-Object name, status, error
