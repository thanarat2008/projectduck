# ProjectDuck launcher - downloads the latest files from GitHub and starts the app as Administrator.
# Edit the 3 lines below, upload this file + projectduck_ui.py + bg.png to the repo, then run:
#   irm https://raw.githubusercontent.com/thanarat2008/projectduck/main/run.ps1 | iex

$User   = 'thanarat2008'
$Repo   = 'projectduck'
$Branch = 'main'          # tip: use a commit hash here to pin an exact version

$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$base = "https://raw.githubusercontent.com/$User/$Repo/$Branch"
$dir  = Join-Path $env:LOCALAPPDATA 'ProjectDuck'      # backups (*_backup.json) are kept here
New-Item -ItemType Directory -Force -Path $dir | Out-Null

function Get-PyCmd {
    foreach ($n in 'pythonw.exe', 'python.exe') {
        $c = Get-Command $n -ErrorAction SilentlyContinue
        if ($c -and $c.Source -notlike '*WindowsApps*') { return $c.Source }
    }
    return $null
}

$py = Get-PyCmd
if (-not $py) {
    Write-Host 'Python not found. Installing Python 3.12 via winget...'
    winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
    Write-Host 'Python installed. Close this window, open a NEW PowerShell and run the same command again.'
    return
}
$pyConsole = $py -replace 'pythonw\.exe$', 'python.exe'

Write-Host 'Checking Pillow...'
& $pyConsole -c "import PIL" 2>$null
if ($LASTEXITCODE -ne 0) { & $pyConsole -m pip install --quiet pillow }

Write-Host 'Downloading ProjectDuck...'
Invoke-WebRequest -UseBasicParsing "$base/projectduck_ui.py" -OutFile (Join-Path $dir 'projectduck_ui.py')
$bg = Join-Path $dir 'bg.png'
if (-not (Test-Path $bg)) { Invoke-WebRequest -UseBasicParsing "$base/bg.png" -OutFile $bg }

Write-Host 'Starting (a UAC prompt will appear)...'
Start-Process -FilePath $py -ArgumentList ('"' + (Join-Path $dir 'projectduck_ui.py') + '"') -WorkingDirectory $dir -Verb RunAs
