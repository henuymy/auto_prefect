// @vitest-environment node

import { describe, expect, it } from "vitest";
import { findLineForPath } from "./jsonPath";

describe("findLineForPath", () => {
  it("resolves duplicate keys using the complete object and array path", () => {
    const value = {
      downloads: [
        {
          name: "first",
          data: { name: "nested-first", note: "contains [brackets] and { braces }" },
        },
        {
          name: "second",
          data: { name: "nested-second" },
        },
      ],
      columns: [
        { field: "first" },
        { field: "second" },
        { field: "third" },
      ],
      compare_sources: [
        {
          download_name: "second",
          sheet_mappings: [{ name: "mapping" }],
        },
      ],
    };
    const lines = JSON.stringify(value, null, 2).split("\n");

    const secondNameLine = lines.findIndex((line) => line.includes('"name": "second"'));
    const nestedSecondNameLine = lines.findIndex((line) => line.includes('"name": "nested-second"'));
    const mappingNameLine = lines.findIndex((line) => line.includes('"name": "mapping"'));
    const thirdFieldLine = lines.findIndex((line) => line.includes('"field": "third"'));

    expect(findLineForPath(lines, ["downloads", 1, "name"])).toBe(secondNameLine);
    expect(findLineForPath(lines, ["downloads", 1, "data", "name"])).toBe(nestedSecondNameLine);
    expect(findLineForPath(lines, ["columns", 2, "field"])).toBe(thirdFieldLine);
    expect(findLineForPath(lines, ["compare_sources", 0, "sheet_mappings", 0, "name"])).toBe(mappingNameLine);
  });

  it("locates primitive and object array items without relying on indentation guesses", () => {
    const value = {
      tags: ["first", "second", "third"],
      nested: [["a"], ["b", "c"]],
    };
    const lines = JSON.stringify(value, null, 2).split("\n");

    expect(findLineForPath(lines, ["tags", 2])).toBe(lines.findIndex((line) => line.includes('"third"')));
    expect(findLineForPath(lines, ["nested", 1, 1])).toBe(lines.findIndex((line) => line.includes('"c"')));
    expect(findLineForPath(lines, ["tags", 99])).toBe(lines.findIndex((line) => line.includes('"tags":')));
    expect(findLineForPath(lines, ["missing", "path"])).toBe(-1);
  });
});
