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
        };

        # Custom derivations copied from the sibling 3dont project.
        requests-sse = pkgs.python3.pkgs.callPackage ./nix/py/requests-sse.nix { };
        qlever-control = pkgs.python3.pkgs.callPackage ./nix/py/qlever-control.nix {
          inherit requests-sse;
        };
        qendpoint = pkgs.callPackage ./nix/qendpoint.nix { };

        # Interpreter used to run the harness itself.
        pythonEnv = pkgs.python3.withPackages (ps: with ps; [
          rich
          psutil
          requests
          rdflib
          sparqlwrapper
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
        packages = { inherit qendpoint qlever-control requests-sse; };

        devShells.default = pkgs.mkShell {
          packages = [ pythonEnv ] ++ nativeEngines ++ [
            pkgs.docker-client   # talk to a system / rootless docker daemon
            pkgs.curl
          ];

          # qEndpoint (Spring Boot) tuning, mirrors 3dont's dev shell.
          JAVA_OPTIONS = "-Dspring.autoconfigure.exclude=org.springframework.boot.autoconfigure.http.client.HttpClientAutoConfiguration -Dspring.devtools.restart.enabled=false";

          shellHook = ''
            export BENCH_DEV=1
            echo "bench dev shell: python + oxigraph/jena/fuseki/qendpoint/qlever + docker-client"
          '';
        };
      });
}
