"use strict";

// Embeddings are disabled in this product, so MemoryCore defers vec0 tables.
// Its SQLite store still requires sqlite-vec during initialization; this
// compatibility module keeps the non-vector SQLite/FTS path active.
exports.load = function load() {};
exports.getLoadablePath = function getLoadablePath() { return ""; };
