$ErrorActionPreference = "Stop"

$ProjectDir = $PSScriptRoot
$PythonExe = Join-Path $ProjectDir ".venv\Scripts\python.exe"
$LogDir = Join-Path $ProjectDir "data\scheduler_logs"

New-Item -ItemType Directory -Path $LogDir -Force | Out-Null

$Timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$LogFile = Join-Path $LogDir "daily_publish_$Timestamp.log"

Set-Location $ProjectDir

& $PythonExe -u "main.py" *> $LogFile
$MainExitCode = $LASTEXITCODE

if ($MainExitCode -ne 0) {
    "`n[$(Get-Date -Format 'HH:mm:ss')] main.py exited with code $MainExitCode, running fallback WeCom notification..." | Out-File -FilePath $LogFile -Append -Encoding utf8
    & $PythonExe -u "send_today_successes.py" *>> $LogFile
}

exit $MainExitCode
