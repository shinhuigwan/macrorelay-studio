param(
    [Parameter(Mandatory = $true)]
    [string]$Root
)

$ErrorActionPreference = "Stop"
$resolvedRoot = (Resolve-Path -LiteralPath $Root).Path
$launcher = Join-Path $resolvedRoot "run_quickslot.ps1"
$icon = Join-Path $resolvedRoot "branding\macrorelay-quickslot.ico"
if (-not (Test-Path -LiteralPath $launcher)) {
    throw "run_quickslot.ps1 파일을 찾을 수 없습니다."
}

$desktop = [Environment]::GetFolderPath("Desktop")
if (-not $desktop) {
    throw "Windows 바탕화면 경로를 찾을 수 없습니다."
}

$shortcutPath = Join-Path $desktop "QuickSlot Deck.lnk"
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = (Get-Command powershell.exe).Source
$shortcut.Arguments = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + $launcher + '"'
$shortcut.WorkingDirectory = $resolvedRoot
$shortcut.Description = "MacroRelay 퀵스트림 터치 컨트롤러"
if (Test-Path -LiteralPath $icon) {
    $shortcut.IconLocation = $icon + ",0"
}
$shortcut.Save()

Write-Output $shortcutPath
