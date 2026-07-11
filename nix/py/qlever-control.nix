{ fetchFromGitHub
, buildPythonApplication
, setuptools
, psutil
, termcolor
, argcomplete
, pyyaml
, rdflib
, requests-sse
}:

buildPythonApplication {
  pname = "qlever-control";
  version = "unstable";
  src = fetchFromGitHub {
    repo = "qlever-control";
    owner = "qlever-dev";
    rev = "8af04bb0f74b7327b38a138a13d3aaa6f96e7f3c";
    sha256 = "sha256-Swnwng6zFbqXPSfuaRHso8FeCh2vuWUG61j0VPpN9LQ=";
  };
  pyproject = true;
  build-system = [ setuptools ];
  dependencies = [
    psutil
    termcolor
    argcomplete
    pyyaml
    rdflib
    requests-sse
  ];
}
