{
  description = "Benchmark harness for RDF / knowledge-store engines";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/dbeacf1b40207b74fede8a8f97a7d6bcf8beee67";
  inputs.flake-utils.url = "github:numtide/flake-utils";

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = import nixpkgs {
          inherit system;
          config.allowUnfree = true;
          overlays = [ rdflibBerkeleyDbFix ];
        };

        # rdflib ships its BerkeleyDB store with five `cursor.next` calls missing their
        # parentheses, which makes the store raise TypeError on any query returning more
        # than one row (see the patch header). Without this, `rdflib-bdb` cannot run —
        # the in-memory `rdflib` engine is unaffected either way.
        rdflibBerkeleyDbFix = final: prev: {
          python3 = prev.python3.override {
            packageOverrides = pyFinal: pyPrev: {
              rdflib = pyPrev.rdflib.overrideAttrs (old: {
                patches = (old.patches or [ ]) ++ [ ./nix/py/rdflib-berkeleydb-cursor.patch ];
              });
            };
          };
        };

        # Custom derivations copied from the sibling 3dont project.
        requests-sse = pkgs.python3.pkgs.callPackage ./nix/py/requests-sse.nix { };
        qlever-control = pkgs.python3.pkgs.callPackage ./nix/py/qlever-control.nix {
          inherit requests-sse;
        };
        qendpoint = pkgs.callPackage ./nix/qendpoint.nix { };
        owlready2 = pkgs.python3.pkgs.callPackage ./nix/py/owlready2.nix { };

        # Curated-minimal TeX Live env shared by the thesis package and the dev shell.
        tex = pkgs.texlive.combine {
          inherit (pkgs.texlive)
            scheme-small              # base: latex, babel, geometry, graphics, hyperref, tools, l3, xcolor
            latexmk                   # build driver
            carlisle                  # plain.sty (\usepackage{plain})
            pdfx xmpincl colorprofiles # PDF/A-1b + sRGB ICC profile
            titlesec setspace wrapfig stackengine listofitems
            xurl enumitem lipsum
            listings
            babel-italian;
        };

        # The thesis PDF, built reproducibly with latexmk.
        thesis = pkgs.callPackage ./nix/thesis.nix { inherit tex; };

        # cspell + Italian dictionary (nixpkgs cspell bundles en/latex but not it-it).
        cspell-it = pkgs.callPackage ./nix/cspell-it.nix { };

        # Interpreter used to run the harness itself (also hosts the rdflib/owlready2
        # embedded engines, wrapped in a tiny HTTP SPARQL server).
        pythonEnv = pkgs.python3.withPackages (ps: with ps; [
          rich
          psutil
          requests
          rdflib
          sparqlwrapper
          owlready2
          berkeleydb   # rdflib persistent store backend
        ]);

        # Native SPARQL engines available as nix packages.
        # Docker-only engines (virtuoso, graphdb, rdfox, stardog) are launched by the
        # harness through the system `docker`/`podman` CLI and need no nix package here.
        nativeEngines = [
          pkgs.oxigraph
          pkgs.apache-jena          # tdb2.tdbloader for bulk loading
          pkgs.apache-jena-fuseki   # fuseki-server HTTP endpoint
          qendpoint                 # qendpoint.sh (HDT build + Spring HTTP endpoint)
          qlever-control            # `qlever` CLI (index + serve)
        ];
      in
      {
        packages = { inherit qendpoint qlever-control requests-sse thesis; };

        devShells.default = pkgs.mkShell {
          packages = [ pythonEnv ] ++ nativeEngines ++ [
            pkgs.docker-client   # talk to a system / rootless docker daemon
            pkgs.curl
            tex                  # latexmk + curated TeX Live for the thesis
            cspell-it            # spellcheck + Italian dict (replaces the template's npm cspell)
          ];

          # qEndpoint (Spring Boot) tuning, mirrors 3dont's dev shell.
          JAVA_OPTIONS = "-Dspring.autoconfigure.exclude=org.springframework.boot.autoconfigure.http.client.HttpClientAutoConfiguration -Dspring.devtools.restart.enabled=false";

          shellHook = ''
            export BENCH_DEV=1
            echo "bench dev shell: python + oxigraph/jena/fuseki/qendpoint/qlever + docker-client + latexmk/cspell"
          '';
        };
      });
}
