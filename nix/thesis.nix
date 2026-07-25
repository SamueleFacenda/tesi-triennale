{ stdenvNoCC, lib, tex }:

stdenvNoCC.mkDerivation {
  pname = "thesis";
  version = "0.1.0";

  src = lib.cleanSource ../thesis;

  nativeBuildInputs = [ tex ];

  env.TZ = "Europe/Rome"; # mirror latexmkrc

  buildPhase = ''
    runHook preBuild
    export HOME=$(mktemp -d)                       # TeX Live in the store is read-only; needs a writable HOME
    export SOURCE_DATE_EPOCH=''${SOURCE_DATE_EPOCH:-0}
    export FORCE_SOURCE_DATE=1                      # deterministic PDF timestamp (pdfx embeds a date)
    latexmk -pdf -interaction=nonstopmode -file-line-error -halt-on-error -outdir=build main
    runHook postBuild
  '';

  installPhase = ''
    runHook preInstall
    install -Dm644 build/main.pdf $out/facenda_thesis.pdf
    runHook postInstall
  '';
}
