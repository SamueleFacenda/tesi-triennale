# cspell wrapped with the Italian dictionary.
#
# The nixpkgs `cspell` bundles en_us/latex/markdown/npm (which thesis/cspell.json
# imports) but not @cspell/dict-it-it. We fetch that dict from the npm registry and
# expose it to cspell's module resolver via NODE_PATH, so the `import` in
# thesis/cspell.json resolves without any npm/node_modules in the tree.
{ lib, stdenvNoCC, fetchurl, cspell, makeWrapper }:

let
  version = "3.1.6";
  dict = stdenvNoCC.mkDerivation {
    pname = "cspell-dict-it-it";
    inherit version;
    src = fetchurl {
      url = "https://registry.npmjs.org/@cspell/dict-it-it/-/dict-it-it-${version}.tgz";
      hash = "sha256-F8gLVJ1zsBGXeTqa5Dh27LDCmZtPS75iHNH4VgbxzNI=";
    };
    dontBuild = true;
    installPhase = ''
      runHook preInstall
      mkdir -p "$out/node_modules/@cspell/dict-it-it"
      cp -r . "$out/node_modules/@cspell/dict-it-it"
      runHook postInstall
    '';
  };
in
stdenvNoCC.mkDerivation {
  pname = "cspell-it";
  inherit (cspell) version;
  dontUnpack = true;
  nativeBuildInputs = [ makeWrapper ];
  installPhase = ''
    runHook preInstall
    mkdir -p "$out/bin"
    makeWrapper ${cspell}/bin/cspell "$out/bin/cspell" \
      --prefix NODE_PATH : "${dict}/node_modules"
    runHook postInstall
  '';
  meta = {
    description = "cspell + Italian dictionary (@cspell/dict-it-it)";
    inherit (cspell.meta or { }) license;
  };
}
