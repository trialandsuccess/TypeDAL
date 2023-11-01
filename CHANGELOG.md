# Changelog

<!--next-version-placeholder-->

## v2.0.0 (2023-11-01)

### Features

* **p4w:** Add py4web specific DAL Fixture ([`2d87327`](https://github.com/trialandsuccess/TypeDAL/commit/2d8732746042b14d02c96c5c3c8e22393bb7697c))
* Added more ORM features (dynamic querying. some better typing etc) ([`64c9b67`](https://github.com/trialandsuccess/TypeDAL/commit/64c9b67085dc821bfc7c90a5e75d5c9489edc4d9))
* More progress on Typed ORM logic. BREAKS A LOT OF PYTESTS!!! ([`582cb56`](https://github.com/trialandsuccess/TypeDAL/commit/582cb56fa122c0cb8ba5df79402af1fab281b535))
* **orm:** Allows creating relationships between tables ([`5902ac6`](https://github.com/trialandsuccess/TypeDAL/commit/5902ac685daeb73c7fd8dd653407763dec18407d))
* Extended Rows (result) functionality ([`8a20c0c`](https://github.com/trialandsuccess/TypeDAL/commit/8a20c0cb4fe8ebc871ff45106c2089b932ad6a03))
* **rows:** Added typedrow functionality + tests ([`22ee99b`](https://github.com/trialandsuccess/TypeDAL/commit/22ee99b493d378075a4a253bad31a92ae8751c99))
* Improvements on row instances ([`218a45e`](https://github.com/trialandsuccess/TypeDAL/commit/218a45eeff5a90dbaeda0a9d82d0873463685516))
* **typedrows:** Added metadata for debugging etc ([`658ecfe`](https://github.com/trialandsuccess/TypeDAL/commit/658ecfe4cb1272605a5e2b395e2cd354d802b965))
* **query:** Improved INNER JOIN handling and allow _method to generate SQL ([`ab5794b`](https://github.com/trialandsuccess/TypeDAL/commit/ab5794b4af863f79dcaa2662c060e8b36c50219f))
* **json:** Compatibility with json-fix ([`d9ca1af`](https://github.com/trialandsuccess/TypeDAL/commit/d9ca1afb09ce58c11b79f71a4c4049049d8f81c4))
* **querybuilder:** Add pagination ([`6adf622`](https://github.com/trialandsuccess/TypeDAL/commit/6adf622bfe7af2bcf197c2a8ea4f962276cc96a1))
* **paginate:** As_dict for PaginatedRows now also returns pagination info in addition to data ([`3b17937`](https://github.com/trialandsuccess/TypeDAL/commit/3b17937b51e68c8bce9c031969702581fd4bcc3b))
* **paginate:** Paginate doesn't return a querybuilder anymore but can replace collect, returns a PaginatedRows ([`ccda5d4`](https://github.com/trialandsuccess/TypeDAL/commit/ccda5d4d9d6e94272472ede0e749fc7b84f46e01))
* **table:** Shadowed and/or modified pydal.Table methods in TypedTable (Meta) ([`397bf67`](https://github.com/trialandsuccess/TypeDAL/commit/397bf675710fc938402151599b0d7887f1ba9d62))
* Add .chunk to query builder + chore more tests ([`51f5979`](https://github.com/trialandsuccess/TypeDAL/commit/51f5979e003733f0f7074e5629d63ac5e3eb71f7))

### Fix

* **json:** Auto add json-fix if including for py4web ([`4d51511`](https://github.com/trialandsuccess/TypeDAL/commit/4d515111dc66071f69848207880cd4c609947747))
* **core:** Table(0) can also work in some instances, so don't check for falsey but for None ([`4430453`](https://github.com/trialandsuccess/TypeDAL/commit/4430453495e4d9e594cd48547707e4d4bb0e2ff2))
* **select:** Auto add missing id in select ([`eacce47`](https://github.com/trialandsuccess/TypeDAL/commit/eacce474f1950e776700899274ffad34e251d6bf))
* **relations:** One-to-one improvements ([`65984ce`](https://github.com/trialandsuccess/TypeDAL/commit/65984cec839351f9800a7a58c06cf95f65b3cf5a))
* **relationships:** Better collection ([`3db63ab`](https://github.com/trialandsuccess/TypeDAL/commit/3db63ab7c6eaa5da5b3639313b5afaa0f62d8ca0))
* **mypy:** Fix return types in fields.py ([`51322f7`](https://github.com/trialandsuccess/TypeDAL/commit/51322f7405cba73fd721c590914a3acde4c6716e))

### Documentation

* Started with more documentation about the ORM features ([`ca86f7c`](https://github.com/trialandsuccess/TypeDAL/commit/ca86f7c271c4e5ee0091ff2818643586f3fe7ce3))
* Minor improvements in examples and text ([`76d0bae`](https://github.com/trialandsuccess/TypeDAL/commit/76d0baee6b6fda551b04032e0f49134bd3be8ef4))
* Updated examples and roadmap ([`55901fd`](https://github.com/trialandsuccess/TypeDAL/commit/55901fd0ee6af1bb9b0e28088c8eb15647f6b8e1))
* **relationships:** Added examples of one-to-many, many-to-one, many-to-many ([`2ff8555`](https://github.com/trialandsuccess/TypeDAL/commit/2ff8555a605b4978c5bfa146bf04d7c3a305b0c0))
* **queries:** Explain select, where, join + new paginated rows ([`6d2b9b8`](https://github.com/trialandsuccess/TypeDAL/commit/6d2b9b8bde26bbbd0e72fc250c0f515b93b2fa89))


## v1.2.2 (2023-07-06)
### Fix

* Allow using `TypedFieldType`s in .select() ([`2945d15`](https://github.com/trialandsuccess/TypeDAL/commit/2945d15b3856f4881d27bd00504b3a2b7363b7ed))

## v1.2.1 (2023-06-27)
### Fix

* Typehint query options for db() and update_or_insert() ([`2b65f58`](https://github.com/trialandsuccess/TypeDAL/commit/2b65f58ccf93f12713877fd420ec43454911efe9))

## v1.2.0 (2023-06-27)
### Feature

* **set:** Type hints for .count() and .select() ([`1f23581`](https://github.com/trialandsuccess/TypeDAL/commit/1f2358169c6690e36153e99983c53a6c370f5e40))

## v1.1.2 (2023-06-27)
### Fix

* **query:** A class with postponed db.define could not be used to make queries ([`2e55363`](https://github.com/trialandsuccess/TypeDAL/commit/2e553632312fd759702e220ae71de9e0abb3d5a9))

## v1.1.1 (2023-06-22)
### Fix

* **mypy:** Made the package properly typed ([`a242895`](https://github.com/trialandsuccess/TypeDAL/commit/a242895d9d8a2d04add9c863105c2b1c756a3b6a))

## v1.1.0 (2023-06-22)
### Feature

* **types:** You should use `param = TypedField()` instead of `param: TypedField()` from now on for better mypy support ([`ed50273`](https://github.com/trialandsuccess/TypeDAL/commit/ed50273ded4a178a30ec8c9d6f492b871853a568))

### Fix

* **version:** We were already on 1.0.0 ([`d6a8d0d`](https://github.com/trialandsuccess/TypeDAL/commit/d6a8d0d17274a5a89e7c0260ba00bb916533866c))

### Documentation

* More info about new release ([`ddfffe2`](https://github.com/trialandsuccess/TypeDAL/commit/ddfffe2c5e6a8f7799449d059e5a2b8dd9c95dab))

## v1.0.0 (2023-06-05)
Refactored pre-1.0 code and added [su6](https://github.com/robinvandernoord/su6-checker) checker tool.  
Moved to `pyproject.toml` based build system.

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
