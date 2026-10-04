$ErrorActionPreference = 'Stop'

# Codex can launch this bridge through a Windows console whose legacy output
# encoding cannot represent every valid UTF-8 Skill or repository value.  MCP
# is line-delimited JSON, so force the child interpreter to use UTF-8 unless a
# caller has already made a stricter explicit choice.
if (-not $env:PYTHONUTF8) {
    $env:PYTHONUTF8 = '1'
}
if (-not $env:PYTHONIOENCODING) {
    $env:PYTHONIOENCODING = 'utf-8'
}

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$bridgePath = Join-Path $scriptRoot 'mcp_server.py'

if (-not (Test-Path -LiteralPath $bridgePath)) {
    throw "SkillSentra MCP bridge was not found: $bridgePath"
}

function Resolve-PythonExecutable {
    param([Parameter(Mandatory = $true)][string]$Candidate)

    if (Test-Path -LiteralPath $Candidate) {
        return (Resolve-Path -LiteralPath $Candidate).Path
    }
    $command = Get-Command $Candidate -ErrorAction SilentlyContinue
    if ($command -and $command.CommandType -in @('Application', 'ExternalScript')) {
        return $command.Source
    }
    return $null
}

function Test-PythonExecutable {
    param([Parameter(Mandatory = $true)][string]$Candidate)

    & $Candidate -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)' 1>$null 2>$null
    return $LASTEXITCODE -eq 0
}

$pythonPath = $null
if ($env:SKILLSENTRA_PYTHON) {
    $pythonPath = Resolve-PythonExecutable -Candidate $env:SKILLSENTRA_PYTHON
    if (-not $pythonPath -or -not (Test-PythonExecutable -Candidate $pythonPath)) {
        throw 'SKILLSENTRA_PYTHON must point to a working Python 3.9+ executable.'
    }
}

if (-not $pythonPath) {
    foreach ($candidate in @('python', 'python3')) {
        $resolved = Resolve-PythonExecutable -Candidate $candidate
        if ($resolved -and (Test-PythonExecutable -Candidate $resolved)) {
            $pythonPath = $resolved
            break
        }
    }
}

if (-not $pythonPath) {
    $profileRoot = if ($env:USERPROFILE) { $env:USERPROFILE } else { [Environment]::GetFolderPath('UserProfile') }
    $bundledPath = Join-Path $profileRoot '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
    if ((Test-Path -LiteralPath $bundledPath) -and (Test-PythonExecutable -Candidate $bundledPath)) {
        $pythonPath = (Resolve-Path -LiteralPath $bundledPath).Path
    }
}

if (-not $pythonPath) {
    throw 'Python 3.9+ was not found. Set SKILLSENTRA_PYTHON or install/load the Codex bundled Python runtime.'
}

& $pythonPath -B -u $bridgePath
exit $LASTEXITCODE
