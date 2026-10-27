param(
  [string]$DataHome,
  [string]$Magpie,
  [switch]$BindOnly
)
$ErrorActionPreference = 'Stop'
if (-not $DataHome) {
  $preferences = Join-Path $env:LOCALAPPDATA 'io.github.wshnbb.deeptutor\desktop.json'
  if (-not (Test-Path -LiteralPath $preferences)) { throw '请先启动 DeepTutor Desktop 并选择应用数据目录。' }
  $DataHome = (Get-Content -LiteralPath $preferences -Raw | ConvertFrom-Json).home
}
$dataRoot = [IO.Path]::GetFullPath($DataHome)
$catalog = Join-Path $dataRoot 'data\user\settings\model_catalog.json'
if (-not (Test-Path -LiteralPath $catalog -PathType Leaf)) { throw "找不到 DeepTutor 模型配置：$catalog" }
# Reserve a separate profile before Magpie writes dotted array fields.
# Existing provider profiles and the active selection stay intact.
$document = Get-Content -LiteralPath $catalog -Raw | ConvertFrom-Json -AsHashtable
$llm = $document.services.llm
$profiles = @($llm.profiles)
if ($llm.profiles -is [System.Collections.IDictionary]) { throw '主模型 profiles 不是 JSON 数组，请先修复该配置。' }
$reserved = @($profiles | Where-Object { $_.id -eq 'llm-magpie-desktop' })
if ($reserved.Count -gt 1) { throw '存在重复的 Magpie 专用配置，请先去除重复项。' }
$changed = $reserved.Count -eq 0 -or $profiles[0].id -ne 'llm-magpie-desktop'
if ($changed) {
  $profile = if ($reserved.Count) { $reserved[0] } else {
    @{
      id='llm-magpie-desktop'; name='Magpie'; binding='openai';
      connection_id=''; base_url=''; api_key=''; api_format='openai_responses';
      wire_api='responses'; proxy=''; extra_headers=@{};
      models=@(@{id='llm-magpie-selected'; name='在 Magpie 中选择模型'; model=''})
    }
  }
  $llm.profiles = @($profile) + @($profiles | Where-Object { $_.id -ne 'llm-magpie-desktop' })
  $temporary = $catalog + '.magpie-bind.tmp'
  [IO.File]::WriteAllText($temporary, ($document | ConvertTo-Json -Depth 50), [Text.UTF8Encoding]::new($false))
  [IO.File]::Move($temporary, $catalog, $true)
}
if (-not $BindOnly -and -not $Magpie) {
  $command = Get-Command magpie -ErrorAction SilentlyContinue
  if ($command) { $Magpie = $command.Source }
  elseif (Test-Path -LiteralPath 'E:\edge\magpie-windows-amd64.exe') { $Magpie = 'E:\edge\magpie-windows-amd64.exe' }
  else { throw '请使用 -Magpie 指定 magpie.exe 的路径。' }
}
$binding = @{ config=$catalog.Replace('\','/'); dir=$dataRoot.Replace('\','/') } | ConvertTo-Json -Compress
[IO.File]::WriteAllText((Join-Path $PSScriptRoot 'binding.js'), ('export const binding = ' + $binding + ';' + [Environment]::NewLine), [Text.UTF8Encoding]::new($false))
if (-not $BindOnly) {
  $configurationRoot = if ($env:XDG_CONFIG_HOME) { $env:XDG_CONFIG_HOME } else { Join-Path $env:USERPROFILE '.config' }
  $pluginDirectory = Join-Path $configurationRoot 'magpie\local-agents\deeptutor'
  New-Item -ItemType Directory -Path $pluginDirectory -Force | Out-Null
  if ([IO.Path]::GetFullPath($PSScriptRoot) -ne [IO.Path]::GetFullPath($pluginDirectory)) {
    foreach ($name in @('package.json','agent.js','binding.js','install.ps1','README.md')) {
      Copy-Item -LiteralPath (Join-Path $PSScriptRoot $name) -Destination (Join-Path $pluginDirectory $name) -Force
    }
  }
  $installed = Start-Process -FilePath $Magpie -ArgumentList @('plugin', 'add', ('"' + $pluginDirectory + '"')) -WindowStyle Hidden -Wait -PassThru
  if ($installed.ExitCode -ne 0) { throw ('Magpie 插件安装失败：' + $installed.ExitCode) }
}
[pscustomobject]@{ agent='deeptutor'; config=$catalog; plugin=$(if($BindOnly){$PSScriptRoot}else{$pluginDirectory}); bound=$true } | ConvertTo-Json -Compress
