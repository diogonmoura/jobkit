import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { decodeEntities } from "./pipeline";

describe("decodeEntities", () => {
  it("decodes each entity once", () => {
    assert.equal(decodeEntities("&lt;p&gt;R&amp;D &quot;team&quot;&nbsp;&#39;26&lt;/p&gt;"), "<p>R&D \"team\" '26</p>");
    assert.equal(decodeEntities("&amp;lt;"), "&lt;");
  });
});
