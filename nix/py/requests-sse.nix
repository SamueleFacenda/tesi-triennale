{ buildPythonPackage
, fetchPypi
, poetry-core
, requests
}:

buildPythonPackage rec {
  pname = "requests_sse";
  version = "0.5.2";
  pyproject = true;
  build-system = [ poetry-core ];
  dependencies = [ requests ];
  src = fetchPypi {
    inherit version pname;
    sha256 = "sha256-K8t8+QUHSxj/n3MicWI0wRiN/egFu6ODALN8a1rjogo=";
  };
}
