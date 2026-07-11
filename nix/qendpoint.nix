{ stdenv
, jre
, makeWrapper
, unzip
, fetchurl
}:

stdenv.mkDerivation (finalAttrs: {
  pname = "qendpoint-cli";
  version = "2.5.2";

  src = fetchurl {
    url = "https://github.com/the-qa-company/qEndpoint/releases/download/v${finalAttrs.version}/qendpoint-cli.zip";
    hash = "sha256-9PIDVBOUBe6AMVvKUMWL9otrJLKTWcuDBkpHvs5cHPw=";
  };

  buildInputs = [ jre ];

  nativeBuildInputs = [ makeWrapper unzip ];

  installPhase = ''
    cp -r . "$out"

    rm "$out"/bin/*.bat
    rm "$out"/bin/*.ps1
    sed -i 's/javaenv\.sh/qendpoint-javaenv.sh/g' $(ls "$out"/bin/*.sh | grep -v javaenv);
    mv "$out"/bin/javaenv.sh "$out"/bin/qendpoint-javaenv.sh

    for i in $(
        find "$out"/bin \
          -type f -name "*.sh" \
          \! -regex '.*\/\(qendpoint\|qep\|[^/]*javaenv\)[^/]*\.sh$' \
          -printf '%f\n' \
      ); do
      i_quotemeta="$(printf '%s\n' "$i" | sed -e 's/[.[\*^$/]/\\&/g')"
      sed -i 's,bin/'"$i_quotemeta"',bin/qendpoint-'"$i"',g' $(ls "$out"/bin/*.sh);
      mv "$out"/bin/$i "$out"/bin/qendpoint-$i
    done

    for i in $(ls "$out"/bin/*.sh | grep -v javaenv); do
      wrapProgram "$i" --prefix "PATH" : "${jre}/bin/" \
        --set-default JAVA_OPTIONS "-Dspring.autoconfigure.exclude=org.springframework.boot.autoconfigure.http.client.HttpClientAutoConfiguration -Dspring.devtools.restart.enabled=false"
    done
  '';
})
