function Get-ProjectPython {
    if ($env:CONDA_PREFIX) {
        $activePython = Join-Path $env:CONDA_PREFIX "python.exe"
        if (Test-Path -LiteralPath $activePython) {
            return $activePython
        }
    }

    $conda = Get-Command conda -ErrorAction SilentlyContinue
    if ($conda) {
        $basePath = (& conda info --base 2>$null).Trim()
        $basePython = Join-Path $basePath "python.exe"
        if ($basePath -and (Test-Path -LiteralPath $basePython)) {
            return $basePython
        }
    }

    return "python"
}
