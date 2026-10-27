# Disposable CI only: exercise the actual native menu and close buttons.
param(
  [Parameter(Mandatory)][int]$DesktopProcessId,
  [Parameter(Mandatory)][ValidateSet('Snapshot','Processes','Settings','Restore','Background','Quit','CloseSettings','CloseTutor','CaptureTutor')][string]$Action,
  [string]$OutputPath
)
$ErrorActionPreference = 'Stop'
if ($Action -eq 'Processes') {
  $all = @(Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,Name)
  $owned = [System.Collections.Generic.HashSet[int]]::new()
  [void]$owned.Add($DesktopProcessId)
  do {
    $changed = $false
    foreach ($process in $all) {
      if ($owned.Contains([int]$process.ParentProcessId) -and $owned.Add([int]$process.ProcessId)) { $changed = $true }
    }
  } while ($changed)
  ConvertTo-Json -InputObject @($all | Where-Object { $owned.Contains([int]$_.ProcessId) } | Select-Object @{n='pid';e={$_.ProcessId}},Name) -Compress
  exit 0
}
Add-Type @'
using System;
using System.Text;
using System.Collections.Generic;
using System.Runtime.InteropServices;
public static class DesktopWindows {
  public delegate bool Callback(IntPtr hwnd, IntPtr param);
  [DllImport("user32.dll")] public static extern bool EnumWindows(Callback callback, IntPtr param);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hwnd, out uint process);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowText(IntPtr hwnd, StringBuilder text, int count);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hwnd);
  [StructLayout(LayoutKind.Sequential)] public struct Rect { public int Left, Top, Right, Bottom; }
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hwnd, out Rect rect);
  [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr hwnd, IntPtr hdc, uint flags);
  [DllImport("user32.dll")] public static extern IntPtr GetMenu(IntPtr hwnd);
  [DllImport("user32.dll")] public static extern int GetMenuItemCount(IntPtr menu);
  [DllImport("user32.dll")] public static extern uint GetMenuItemID(IntPtr menu, int position);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetMenuString(IntPtr menu, uint item, StringBuilder text, int count, uint flags);
  [DllImport("user32.dll")] public static extern bool PostMessage(IntPtr hwnd, uint message, IntPtr wparam, IntPtr lparam);
  public static IntPtr[] Find(int process) {
    var result = new List<IntPtr>();
    EnumWindows((hwnd, _) => { uint owner; GetWindowThreadProcessId(hwnd, out owner); if(owner == process) result.Add(hwnd); return true; }, IntPtr.Zero);
    return result.ToArray();
  }
  public static string Title(IntPtr hwnd) { var text = new StringBuilder(512); GetWindowText(hwnd, text, text.Capacity); return text.ToString(); }
}
'@
$windows = @([DesktopWindows]::Find($DesktopProcessId) | ForEach-Object {
  [pscustomobject]@{ handle = $_; title = [DesktopWindows]::Title($_); visible = [DesktopWindows]::IsWindowVisible($_) }
})
if ($Action -eq 'Snapshot') {
  ConvertTo-Json -InputObject @($windows | Select-Object title,visible) -Compress
  exit 0
}
$menuAction = $Action -in @('Settings','Restore','Background','Quit')
$title = if ($Action -eq 'CloseSettings' -or ($menuAction -and -not ($windows | Where-Object title -eq 'DeepTutor'))) { 'DeepTutor 桌面设置' } else { 'DeepTutor' }
$target = @($windows | Where-Object { $_.title -eq $title })
if ($target.Count -ne 1) { throw "Expected one window: $title" }
if ($Action -eq 'CaptureTutor') {
  if (-not $OutputPath) { throw 'Capture requires an output path' }
  Add-Type -AssemblyName System.Drawing
  $rect = [DesktopWindows+Rect]::new()
  if (-not [DesktopWindows]::GetWindowRect($target[0].handle, [ref]$rect)) { throw 'Cannot read window bounds' }
  $bitmap = [System.Drawing.Bitmap]::new($rect.Right - $rect.Left, $rect.Bottom - $rect.Top)
  $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
  $hdc = $graphics.GetHdc()
  try { $captured = [DesktopWindows]::PrintWindow($target[0].handle, $hdc, 2) }
  finally { $graphics.ReleaseHdc($hdc) }
  try {
    if (-not $captured) { throw 'Native window capture failed' }
    $bitmap.Save($OutputPath, [System.Drawing.Imaging.ImageFormat]::Png)
  } finally { $graphics.Dispose(); $bitmap.Dispose() }
} elseif ($menuAction) {
  $prefix = @{Settings='桌面设置';Restore='显示学习界面';Background='后台运行';Quit='退出 DeepTutor'}[$Action]
  $menu = [DesktopWindows]::GetMenu($target[0].handle)
  $found = $false
  for ($index = 0; $index -lt [DesktopWindows]::GetMenuItemCount($menu); $index++) {
    $text = [System.Text.StringBuilder]::new(256)
    [void][DesktopWindows]::GetMenuString($menu, $index, $text, $text.Capacity, 0x400)
    if ($text.ToString().StartsWith($prefix)) {
      $id = [DesktopWindows]::GetMenuItemID($menu, $index)
      if ($id -eq [uint32]::MaxValue) { throw 'Desktop menu is not a command' }
      if (-not [DesktopWindows]::PostMessage($target[0].handle, 0x111, [IntPtr]::new($id), [IntPtr]::Zero)) { throw 'Menu command failed' }
      $found = $true
      break
    }
  }
  if (-not $found) { throw "Native desktop menu is missing: $prefix" }
} else {
  if (-not [DesktopWindows]::PostMessage($target[0].handle, 0x10, [IntPtr]::Zero, [IntPtr]::Zero)) { throw 'Window close failed' }
}
