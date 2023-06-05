# Changelog

<!--next-version-placeholder-->

## v1.0.0-beta.1 (2023-06-05)
### Feature
* **su6:** Add github workflow with checkers ([`d462387`](https://github.com/trialandsuccess/TypeDAL/commit/d46238705b27137ed60806eca5c53d8c2940052f))
* **tests:** Moved tests.py to pytest ([`de33d20`](https://github.com/trialandsuccess/TypeDAL/commit/de33d2053e6499c4a8628cfc4b8813b5a649c584))
* **define:** Pass extra kwargs (e.g. format) to define_table ([`bb692dc`](https://github.com/trialandsuccess/TypeDAL/commit/bb692dc1b2a9c18e45bcb70771b7435df8a39c8e))
* **TypedRows:** Subscriptable return type for .select() ([`7b674b0`](https://github.com/trialandsuccess/TypeDAL/commit/7b674b07d4e61f4ead5d3f006e018b1aa66c10e7))
* Add specific Fields, experimented with __future__.annotations (does not work right now) and general improvements ([`9af27c2`](https://github.com/trialandsuccess/TypeDAL/commit/9af27c254bf2ad7bd3bfa80e66b6e08e82f8f13a))

### Fix
* **gh:** Also install current project with dependencies on checking ([`60a1010`](https://github.com/trialandsuccess/TypeDAL/commit/60a101064b32cead856dd1a0d1a78a2e74c94abf))
* **ruff:** Make linter happy ([`b961054`](https://github.com/trialandsuccess/TypeDAL/commit/b9610542d199a42b527a58aaf08ddf70025abe6e))
* **..Field:** Use typing.Type[] instead of type() for better hinting ([`308ea14`](https://github.com/trialandsuccess/TypeDAL/commit/308ea14f38d4611b626c0edaf737ee53467b25b2))
* **core:** Table(id) works now just like Table(id=id) ([`c19e170`](https://github.com/trialandsuccess/TypeDAL/commit/c19e17074edfba06287402d0c52d52127fe15499))
* **intfield:** Return int type, not int instance ([`334c3ac`](https://github.com/trialandsuccess/TypeDAL/commit/334c3acd9f0e38badb742a69181e19ebf2088cb1))
* **core:** Notnull > required, fix ReferenceField with cls instead of string ([`1cc1482`](https://github.com/trialandsuccess/TypeDAL/commit/1cc148209572ce1d457bfde51f86c9167b2da904))
* Actually use custom type in TypedField ([`98c8fc3`](https://github.com/trialandsuccess/TypeDAL/commit/98c8fc30c311136b4804ed3211884b70dd7a98af))
