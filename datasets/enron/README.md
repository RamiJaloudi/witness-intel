# Optional Enron benchmark

The benchmark generator pins the May 7, 2015 [CMU Enron release](https://www.cs.cmu.edu/~enron/) by SHA-256. The generated benchmark uses 200 messages and a separate answer key for metadata and literal quotations. CMU asks researchers to respect the privacy of the people in the corpus.

From the repository root in an activated Python environment:

```powershell
python -m scripts.prepare_enron --download
python -m scripts.prepare_enron --validate
```

The generator writes the source archive, selected messages and answer key to ignored local directories. Use the fictional Glenron demo for a standalone example. If you use the optional benchmark, keep the answer key out of evidence imports and model inputs. For import prerequisites, see [Glenron testing](../../docs/GLENRON_TESTING.md).
