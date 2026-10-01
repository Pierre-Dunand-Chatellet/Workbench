# Construit ..\dist\Workbench\Workbench.exe (dans le dossier du projet, à côté de src\) puis le vérifie.
#   .\build.ps1            construit et teste
#   .\build.ps1 -SansTest  construit seulement
#
# Le .exe embarque ffmpeg + ffprobe (build « full » de gyan.dev, celui installé sur cette
# machine, ~445 Mo) dans dist\Workbench\ffmpeg\ : rien à installer sur la machine cible.
# Le modèle u2net (176 Mo) n'est pas embarqué : il se télécharge au premier détourage.
param([switch]$SansTest)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$nom = "Workbench"
# Sortie dans le dossier du projet, pas dans src\ : avant, Set-Location + "dist" posait
# l'exe dans src\dist alors que le raccourci et le README pointent sur ..\dist.
$dist = Join-Path (Split-Path $PSScriptRoot -Parent) "dist"

Write-Host "== nettoyage ==" -ForegroundColor Cyan
# seulement NOTRE sortie dans ..\dist : les autres dossiers qui s'y trouvent ne sont pas à nous
foreach ($d in @("build", "__pycache__", "$dist\$nom")) {
    if (Test-Path $d) { Remove-Item $d -Recurse -Force }
}

Write-Host "== PyInstaller ==" -ForegroundColor Cyan
# --collect-all : tkinterdnd2 embarque des .tcl/.dll que l'analyse statique ne voit pas,
# onnxruntime ses bibliothèques natives.
# PyInstaller écrit sa progression sur stderr ; en PowerShell 5.1 cela suffit à déclencher
# une NativeCommandError, d'où le passage en "Continue" le temps de l'appel.
$ErrorActionPreference = "Continue"

# Le hook onnxruntime déclare onnxruntime.transformers et .tools en imports cachés. On n'en
# utilise rien (juste InferenceSession), et ils traînent numba -> llvmlite (115 Mo) + scipy :
# exactement la chaîne qu'on avait fuie en n'important pas rembg. On les coupe.
$inutiles = @(
    "onnxruntime.transformers", "onnxruntime.tools", "onnxruntime.quantization",
    "numba", "llvmlite", "scipy", "cryptography", "jinja2", "psutil",
    "win32com", "Pythonwin", "pandas", "matplotlib", "IPython", "pytest"
)
$exclus = $inutiles | ForEach-Object { "--exclude-module", $_ }

# icone du .exe, generee depuis image_logo(): toujours aux couleurs du code, jamais un .ico perime
python -c "import Workbench; Workbench.image_logo(256).save('logo.ico', sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])"
if ($LASTEXITCODE -ne 0) { $ErrorActionPreference = "Stop"; throw "icone non generee" }
python -m PyInstaller --noconfirm --windowed --clean `
    --name $nom `
    --icon logo.ico `
    --distpath $dist `
    --collect-all tkinterdnd2 `
    --collect-binaries onnxruntime `
    --collect-data onnxruntime `
    @exclus `
    Workbench.py
$code = $LASTEXITCODE
$ErrorActionPreference = "Stop"
if ($code -ne 0) { throw "PyInstaller a échoué (code $code)" }

# ponytail: on retire la base de fuseaux horaires de Tcl (77 Mo). Tkinter ne s'en sert pas,
# seul `clock` avec un fuseau la lit, et l'appli n'appelle jamais `clock`. Si un jour une
# date localisée apparaît dans l'interface, supprimer ces trois lignes.
# (Les dates de l'outil Archives sont formatées en Python, pas par `clock`.)
$tz = "$dist\$nom\_internal\_tcl_data\tzdata"
if (Test-Path $tz) { Remove-Item $tz -Recurse -Force }

# ffmpeg embarqué : on copie celui de cette machine (le lien winget pointe sur le vrai
# dossier). ffplay n'est pas utilisé. La licence GPL voyage avec les binaires, c'est une
# condition de leur redistribution.
$lien = (Get-Command ffmpeg -ErrorAction SilentlyContinue).Source
if (-not $lien) { throw "ffmpeg introuvable sur cette machine : impossible de l'embarquer" }
$cible = (Get-Item $lien).Target
$bin = Split-Path $(if ($cible) { @($cible)[0] } else { $lien })
$ff = "$dist\$nom\ffmpeg"
New-Item -ItemType Directory $ff -Force | Out-Null
Copy-Item "$bin\ffmpeg.exe", "$bin\ffprobe.exe" $ff
$licence = Join-Path (Split-Path $bin) "LICENSE"
if (Test-Path $licence) { Copy-Item $licence "$ff\LICENSE.txt" }
Write-Host "ffmpeg embarqué depuis $bin" -ForegroundColor Cyan

# polices du portfolio (Syne, Geist, JetBrains Mono, licence OFL) : à côté du .exe, comme ffmpeg. Workbench.py les
# enregistre au démarrage pour son seul processus ; LICENCES.txt voyage avec elles, c'est une condition de la licence.
Copy-Item "$PSScriptRoot\fonts" "$dist\$nom\fonts" -Recurse -Force
Write-Host "polices copiées dans $dist\$nom\fonts" -ForegroundColor Cyan

# intermédiaires : régénérés à chaque build, ils n'ont rien à faire dans le dossier.
# La source de vérité est CE script, pas le .spec que PyInstaller vient d'écrire.
Remove-Item "build" -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item "$nom.spec" -Force -ErrorAction SilentlyContinue
Remove-Item "logo.ico" -Force -ErrorAction SilentlyContinue
Remove-Item "__pycache__" -Recurse -Force -ErrorAction SilentlyContinue

$exe = "$dist\$nom\$nom.exe"
$taille = [math]::Round((Get-ChildItem "$dist\$nom" -Recurse -File | Measure-Object Length -Sum).Sum / 1MB)
Write-Host "== construit : $exe ($taille Mo) ==" -ForegroundColor Green

if ($SansTest) { return }

Write-Host "== vérification (--smoke) ==" -ForegroundColor Cyan
$journal = "$env:TEMP\workbench_smoke.txt"
Remove-Item $journal -ErrorAction SilentlyContinue
$p = Start-Process $exe -ArgumentList "--smoke" -PassThru
if (-not $p.WaitForExit(180000)) { $p | Stop-Process -Force; throw "le .exe ne répond pas" }
if (-not (Test-Path $journal)) { throw "aucun journal : le .exe n'a pas démarré" }
Get-Content $journal
if ((Get-Content $journal -Raw) -notmatch "SMOKE OK") { throw "vérification échouée" }
Write-Host "== OK ==" -ForegroundColor Green
