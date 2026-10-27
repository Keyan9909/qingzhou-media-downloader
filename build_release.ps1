param(
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"
$Arguments = @("$PSScriptRoot\tools\build_release.py")
if ($SkipTests) {
    $Arguments += "--skip-tests"
}
& python @Arguments
exit $LASTEXITCODE
