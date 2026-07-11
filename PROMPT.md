Take enspiration from the code in @../3dont , you can entirely copy the nix files. 
I need to write an automatic benchmark software, for knowledge store engines.
Some queries will be defined, take some of the queries in 3dont as example (like select all), and provide an easy way
to add new queries. These are the engines that need to be tested:
		- [qlever](https://github.com/ad-freiburg/qlever)
		- [qendpoint](https://github.com/the-qa-company/qEndpoint)
		- [oxigraph](https://github.com/oxigraph/oxigraph)
		- [virtuoso open](https://github.com/openlink/virtuoso-opensource)
		- [rdfox](https://www.oxfordsemantic.tech/) closed source
		- [graphdb](https://graphdb.ontotext.com/)
		- [stardog](https://www.stardog.com) maybe not self hostable
		- blazegraph is dead
		- [apache jena/fuseki](https://jena.apache.org/documentation/fuseki2/)
So write a virtual interface for them in order to have an easy way to run the same queries on different datasets.
I will provide some dataset sources, in ntriples format, rdf xml or turtle. The queries needs to be ran on each dataset,
keep in mind that they might use different base ontologies (as you see in 3dont).
I want a persistent benchmark, that I can stop at any time and go on and outputs the results in machine readible formats.
Store everything in a directory, intermediate results, current point of benchmark, individual databases storages.
Use native packages if possible (always with nix), docker packages otherwise if possible. Try to benchmark the
connection/serialization overhead, or to minimize the differences caused by the embedded/http connection (or use http for
each of them, adding a http+serialization step if not present).
So the benchmark will run each query against each database against each engine a certain number of times (a parameter, 10 default).
Write clean code, it's a benchmark so parallel queries should not happen (max resources per query), also try to avoid cold runs.
Also provide some loading bars/basic tui interface. All the db must be self hostable/locally ran, if not possible skip them.
If there are other relevant engines feel free to add them (only big/innovative ones).
Here some of the queries that can be ran:
		- LDBC Semantic Publishing Benchmark advanced query 2
		- initial select all
		- scalar: select all permeability
		- select: select all points in an object
		- chained segmentation query
		- max lod query
		- tassonomical hierarchy query
		- which is the average height of the objects?
In @~/downloads/ontos/ you can find some populated ontologies (caution, very big files, avoid storing them here also for copyright issues).
In @~/.local/share/threedont/projects you can find the 3dont project files, with base ontology and other infos.
