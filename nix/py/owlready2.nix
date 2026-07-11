{ buildPythonPackage
, fetchPypi
, distutils
, setuptools
, cython 
}:

buildPythonPackage rec {
  pname = "owlready2";
  version = "0.47";
  pyproject = true;
  build-system = [ setuptools ];
  src = fetchPypi {
    inherit pname version;
    hash = "sha256-r34dIgXAtYhtLjQ5erjBDKKf9ow9w3AtQzk5Zqx/brA=";
  };
  dependencies = [
    distutils
    cython
  ];
}
