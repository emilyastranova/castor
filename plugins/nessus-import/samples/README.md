# Sample .nessus files

Test data for the Nessus import plugin. Neither file was made by Castor; both are kept unchanged
under their projects' MIT licences (copied next to them).

| File | From | Licence | What's in it |
| --- | --- | --- | --- |
| `cerno-demo-expanded.nessus` | [ridgebackinfosec/cerno](https://github.com/ridgebackinfosec/cerno/blob/f36b5ea54b932959edad8ea047a9d3c8d0fd9e54/docs/demo/cerno-demo-expanded.nessus) `docs/demo/cerno-demo-expanded.nessus` | MIT, © 2025 Ridgeback Information Security ([LICENSE-cerno](LICENSE-cerno)) | A synthetic scan: 42 hosts, 125 findings, all in the `198.51.100.0/24` documentation range and `*.demo.example`. Many hosts and services, sparse host details (address, FQDN, some NetBIOS names). |
| `ruby-nessus-example-v3.nessus` | [Cyberwatch/ruby-nessus](https://github.com/Cyberwatch/ruby-nessus/blob/3cc208775855001bfa7a7556dd373b08c99aa268/examples/example_v3.nessus) `examples/example_v3.nessus` | MIT, © 2013 Dustin Willis Webber ([LICENSE-ruby-nessus](LICENSE-ruby-nessus)) | A real Nessus v2 export of one host (`10.0.0.1`), 155 findings, with the scan policy, every host property, CVSS, CVEs and plugin output. |

SHA-256:

```text
71f837ef5e58a81a8859a5f4cd7cbc43c9da3e9c24f8cf0bbadcb8a9e5ecca0d  cerno-demo-expanded.nessus
4e2789de2e1b8e2dc5e2ebd2b749e1ee70e81af5e4b0565099dc2005879fac00  ruby-nessus-example-v3.nessus
```

Both are data from other projects, so treat them as untrusted input like any scan file.
