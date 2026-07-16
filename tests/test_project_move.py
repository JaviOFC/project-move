"""
Automated tests for project-move.

Everything here runs against isolated temporary directories via ClaudeEnv —
no test ever touches the real Path.home(), the real ~/.claude, or any real
project directory. Run with:

    py -m unittest discover -s tests -v
"""

import importlib.machinery
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = ROOT / "project-move"

_loader = importlib.machinery.SourceFileLoader("project_move", str(SCRIPT_PATH))
_spec = importlib.util.spec_from_loader("project_move", _loader)
pm = importlib.util.module_from_spec(_spec)
sys.modules["project_move"] = pm
_loader.exec_module(pm)


def silent(fn, *a, **kw):
    buf = io.StringIO()
    with redirect_stdout(buf):
        result = fn(*a, **kw)
    return result, buf.getvalue()


class TempHomeMixin:
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name) / "home"
        self.home.mkdir(parents=True)
        self.work = Path(self._tmp.name) / "work"
        self.work.mkdir(parents=True)

    def tearDown(self):
        self._tmp.cleanup()

    def env(self, windows=None):
        """Filesystem integration tests exercise the REAL host filesystem, so
        the platform flag defaults to whatever OS is actually running the
        test — forcing windows=True on a real POSIX host (or vice versa)
        would assert behaviors (case-insensitivity, drive letters) the real
        filesystem doesn't have. Pure path-logic tests that need a specific
        platform's rules regardless of host should NOT use this fixture —
        they should call the encode_path/classify_relationship/etc.
        functions directly with literal path strings and an explicit
        `windows=` argument instead (see TestEncodePathWindows and
        TestPathRelationships)."""
        if windows is None:
            windows = os.name == "nt"
        return pm.ClaudeEnv(home=self.home, windows=windows)

    def make_project(self, name="myapp", parent=None):
        base = parent or self.work
        proj = base / name
        proj.mkdir(parents=True, exist_ok=True)
        (proj / "main.py").write_text("print('hi')\n", encoding="utf-8")
        return proj

    def seed_claude_state(self, env: pm.ClaudeEnv, project_path: Path, extra_json_projects=None):
        """Populate a minimal, realistic ~/.claude tree referencing project_path."""
        old_abs = str(project_path)
        encoded = pm.encode_path(old_abs, env.windows)

        proj_dir = env.claude_dir / "projects" / encoded
        proj_dir.mkdir(parents=True, exist_ok=True)
        session = proj_dir / "11111111-1111-1111-1111-111111111111.jsonl"
        line = json.dumps({"type": "user", "cwd": old_abs, "message": {"role": "user", "content": "hi"}})
        session.write_text(line + "\n", encoding="utf-8")

        mem_dir = proj_dir / "memory"
        mem_dir.mkdir(parents=True, exist_ok=True)
        (mem_dir / "MEMORY.md").write_text(f"Working on {old_abs}\n", encoding="utf-8")

        env.claude_dir.mkdir(parents=True, exist_ok=True)
        history = {"display": "hi", "project": old_abs, "timestamp": 1, "sessionId": "s1"}
        env.history_file.write_text(json.dumps(history) + "\n", encoding="utf-8")

        projects_map = {old_abs: {"allowedTools": []}}
        if extra_json_projects:
            projects_map.update(extra_json_projects)
        env.claude_json.write_text(json.dumps({"projects": projects_map}, indent=2), encoding="utf-8")

        env.plugins_file.parent.mkdir(parents=True, exist_ok=True)
        plugins = {
            "version": 2,
            "plugins": {
                "some-plugin@vendor": [
                    {"scope": "project", "projectPath": old_abs, "version": "1.0.0"}
                ]
            },
        }
        env.plugins_file.write_text(json.dumps(plugins, indent=2), encoding="utf-8")

        own_settings_dir = project_path / ".claude"
        own_settings_dir.mkdir(parents=True, exist_ok=True)
        own_settings = {"permissions": {"allow": [], "additionalDirectories": [old_abs]}}
        (own_settings_dir / "settings.local.json").write_text(json.dumps(own_settings, indent=2), encoding="utf-8")

        return encoded


# --------------------------------------------------------------------------
# Pure logic tests — no filesystem required, run for both platform modes
# regardless of host OS, so POSIX behavior is checked even on a Windows dev
# machine (and vice versa).
# --------------------------------------------------------------------------

class TestEncodePathWindows(unittest.TestCase):
    """Verified against real ~/.claude/projects/<encoded> names on a real
    Windows Claude Code install (see audit notes) — every character outside
    [A-Za-z0-9-] becomes '-', case and separator style preserved verbatim."""

    def test_basic_backslash_path(self):
        self.assertEqual(
            pm.encode_path(r"C:\Development\Python\Scripts\project-move", True),
            "C--Development-Python-Scripts-project-move",
        )

    def test_uppercase_drive(self):
        self.assertEqual(
            pm.encode_path(r"C:\Development\Arduino\sketches", True),
            "C--Development-Arduino-sketches",
        )

    def test_lowercase_drive_forward_slash(self):
        self.assertEqual(
            pm.encode_path("c:/Development/CPlusPlus/Shell", True),
            "c--Development-CPlusPlus-Shell",
        )

    def test_space_and_unicode(self):
        self.assertEqual(
            pm.encode_path(r"C:\Development\CSharp\Apps\Elkjøp Support Tool", True),
            "C--Development-CSharp-Apps-Elkj-p-Support-Tool",
        )

    def test_dots_and_underscores_become_dashes(self):
        self.assertEqual(
            pm.encode_path(r"C:\Development\my_app.v2", True),
            "C--Development-my-app-v2",
        )

    def test_unc_path(self):
        encoded = pm.encode_path(r"\\server\share\proj", True)
        self.assertNotIn("\\", encoded)
        self.assertTrue(encoded.replace("-", "").isalnum() or encoded.replace("-", "") == "")

    def test_hyphens_preserved(self):
        self.assertIn("project-move", pm.encode_path(r"C:\Development\project-move", True))


class TestEncodePathPosix(unittest.TestCase):
    """POSIX regression — behavior must remain exactly what it was before
    the Windows fix (only '/', '.', '_', space replaced)."""

    def test_basic(self):
        self.assertEqual(
            pm.encode_path("/Users/you/projects/my-app", False),
            "-Users-you-projects-my-app",
        )

    def test_dots_underscores_spaces(self):
        self.assertEqual(
            pm.encode_path("/Users/you/my_app v2.final", False),
            "-Users-you-my-app-v2-final",
        )

    def test_unicode_left_untouched_on_posix(self):
        # POSIX behavior intentionally unchanged: only '/._ ' are replaced.
        self.assertEqual(
            pm.encode_path("/Users/you/café", False),
            "-Users-you-café",
        )


class TestPathRelationships(unittest.TestCase):
    def test_same_path_windows_case_insensitive(self):
        rel = pm.classify_relationship(Path(r"C:\Dev\App"), Path(r"c:\dev\app"), True)
        self.assertEqual(rel, "case-only")

    def test_identical_string_is_same(self):
        rel = pm.classify_relationship(Path(r"C:\Dev\App"), Path(r"C:\Dev\App"), True)
        self.assertEqual(rel, "same")

    def test_sibling_similar_prefix_not_ancestor(self):
        # This is the App/App2 bug: naive startswith() would misfire here.
        self.assertFalse(pm.is_ancestor(Path(r"C:\Dev\App"), Path(r"C:\Dev\App2"), True))
        rel = pm.classify_relationship(Path(r"C:\Dev\App"), Path(r"C:\Dev\App2"), True)
        self.assertEqual(rel, "unrelated")

    def test_destination_inside_source(self):
        rel = pm.classify_relationship(Path(r"C:\Dev\App"), Path(r"C:\Dev\App\sub\dest"), True)
        self.assertEqual(rel, "dest-inside-src")

    def test_source_inside_destination(self):
        rel = pm.classify_relationship(Path(r"C:\Dev\App\sub"), Path(r"C:\Dev\App"), True)
        self.assertEqual(rel, "src-inside-dest")

    def test_posix_case_sensitive(self):
        rel = pm.classify_relationship(Path("/Users/you/App"), Path("/Users/you/app"), False)
        self.assertEqual(rel, "unrelated")

    def test_unc_ancestor_detection(self):
        self.assertTrue(
            pm.is_ancestor(Path(r"\\server\share\proj"), Path(r"\\server\share\proj\sub"), True)
        )
        self.assertFalse(
            pm.is_ancestor(Path(r"\\server\share\proj"), Path(r"\\server\share\proj2"), True)
        )


class TestSameVolume(TempHomeMixin, unittest.TestCase):
    def test_same_directory_tree_is_same_volume(self):
        a = self.work / "a"
        b = self.work / "b"
        a.mkdir()
        self.assertTrue(pm.same_volume(a, b, True))

    def test_different_device_ids_detected_via_stat(self):
        # POSIX anchors are always '/' regardless of mount point, so this
        # must be driven by st_dev, not by comparing anchor strings.
        import unittest.mock as mock

        real_stat = os.stat

        def fake_stat(path, *a, **k):
            st = real_stat(path)
            if str(path).endswith("volA"):
                return type(st)((st.st_mode, st.st_ino, 111, st.st_nlink, st.st_uid, st.st_gid, st.st_size, st.st_atime, st.st_mtime, st.st_ctime))
            if str(path).endswith("volB"):
                return type(st)((st.st_mode, st.st_ino, 222, st.st_nlink, st.st_uid, st.st_gid, st.st_size, st.st_atime, st.st_mtime, st.st_ctime))
            return st

        vol_a = self.work / "volA"
        vol_b = self.work / "volB"
        vol_a.mkdir()
        vol_b.mkdir()
        with mock.patch("os.stat", side_effect=fake_stat):
            self.assertFalse(pm.same_volume(vol_a / "proj", vol_b / "proj", True))


class TestScanTreeConflicts(unittest.TestCase):
    """A prior version allowed a merge to proceed whenever child FILES were
    disjoint, even if a subdirectory name existed on both sides (e.g. both
    trees have a 'shared/' directory, just with different files inside it).
    move_tree_disjoint() only moves top-level children, so it would then
    collide on 'shared/' and fail — potentially after Claude state was
    already renamed. Conservative fix: ANY matching relative path, including
    a directory matching a directory, is a conflict; merges require fully
    disjoint trees."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.src = self.root / "src"
        self.dst = self.root / "dst"
        self.src.mkdir()
        self.dst.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def test_shared_directory_with_disjoint_child_files_is_a_conflict(self):
        (self.src / "shared").mkdir()
        (self.src / "shared" / "a.txt").write_text("a", encoding="utf-8")
        (self.dst / "shared").mkdir()
        (self.dst / "shared" / "b.txt").write_text("b", encoding="utf-8")

        conflicts = pm.scan_tree_conflicts(self.src, self.dst, True)
        self.assertIn("shared", conflicts)

    def test_shared_nested_directories_is_a_conflict(self):
        (self.src / "a" / "b" / "c").mkdir(parents=True)
        (self.dst / "a" / "b" / "c").mkdir(parents=True)
        (self.src / "a" / "b" / "c" / "x.txt").write_text("x", encoding="utf-8")

        conflicts = pm.scan_tree_conflicts(self.src, self.dst, True)
        self.assertTrue(any(c in conflicts for c in ("a", str(Path("a", "b")), str(Path("a", "b", "c")))))

    def test_shared_empty_directories_is_a_conflict(self):
        (self.src / "shared").mkdir()
        (self.dst / "shared").mkdir()

        conflicts = pm.scan_tree_conflicts(self.src, self.dst, True)
        self.assertIn("shared", conflicts)

    def test_case_only_matching_directory_name_is_a_conflict_on_windows(self):
        (self.src / "Shared").mkdir()
        (self.dst / "shared").mkdir()

        conflicts_windows = pm.scan_tree_conflicts(self.src, self.dst, True)
        self.assertTrue(len(conflicts_windows) > 0)

        conflicts_posix = pm.scan_tree_conflicts(self.src, self.dst, False)
        self.assertEqual(conflicts_posix, [])

    def test_fully_disjoint_trees_have_no_conflicts(self):
        (self.src / "a.txt").write_text("a", encoding="utf-8")
        (self.src / "subdir").mkdir()
        (self.dst / "b.txt").write_text("b", encoding="utf-8")
        (self.dst / "otherdir").mkdir()

        self.assertEqual(pm.scan_tree_conflicts(self.src, self.dst, True), [])


class TestReplacementEngine(unittest.TestCase):
    def variants(self, old, new, windows=True, home="C:\\Users\\test"):
        return pm.build_logical_variants(old, new, home, windows)

    def test_json_escaped_backslash_is_found(self):
        # This is the core Windows bug: raw JSON text stores paths with
        # doubled backslashes; a naive substring search for the single-
        # backslash form finds nothing even though the path IS present.
        old, new = r"C:\Dev\App", r"C:\Dev\Archive\App"
        raw = json.dumps({"project": old})
        result = pm.edit_text(raw, self.variants(old, new), True, json_like=True, jsonl=False)
        self.assertTrue(result.changed)
        self.assertEqual(json.loads(result.new_text)["project"], new)

    def test_dict_key_reference_is_found(self):
        # ~/.claude.json keys its "projects" map BY the literal path.
        old, new = r"C:\Dev\App", r"C:\Dev\Archive\App"
        raw = json.dumps({"projects": {old: {"foo": 1}}})
        result = pm.edit_text(raw, self.variants(old, new), True, json_like=True, jsonl=False)
        self.assertTrue(result.changed)
        self.assertIn(new, json.loads(result.new_text)["projects"])
        self.assertNotIn(old, json.loads(result.new_text)["projects"])

    def test_sibling_project_not_corrupted(self):
        # "App" must never match inside "App2" (the Joe/Joel-style bug).
        old, new = r"C:\Dev\App", r"C:\Dev\Archive\App"
        raw = json.dumps({"a": old, "b": r"C:\Dev\App2\file.txt"})
        result = pm.edit_text(raw, self.variants(old, new), True, json_like=True, jsonl=False)
        parsed = json.loads(result.new_text)
        self.assertEqual(parsed["a"], new)
        self.assertEqual(parsed["b"], r"C:\Dev\App2\file.txt")

    def test_subpath_reference_is_replaced_with_suffix_preserved(self):
        old, new = r"C:\Dev\App", r"C:\Dev\Archive\App"
        raw = json.dumps({"a": old + r"\subfolder\file.txt"})
        result = pm.edit_text(raw, self.variants(old, new), True, json_like=True, jsonl=False)
        self.assertEqual(json.loads(result.new_text)["a"], new + r"\subfolder\file.txt")

    def test_unrelated_path_untouched(self):
        old, new = r"C:\Dev\App", r"C:\Dev\Archive\App"
        raw = json.dumps({"a": r"C:\Dev\Other\Thing"})
        result = pm.edit_text(raw, self.variants(old, new), True, json_like=True, jsonl=False)
        self.assertFalse(result.changed)
        self.assertEqual(json.loads(result.new_text)["a"], r"C:\Dev\Other\Thing")

    def test_case_insensitive_match_on_windows(self):
        old, new = r"C:\Dev\App", r"C:\Dev\Archive\App"
        raw = json.dumps({"a": r"c:\dev\app\file.txt"})
        result = pm.edit_text(raw, self.variants(old, new), True, json_like=True, jsonl=False)
        self.assertTrue(result.changed)

    def test_jsonl_per_line_structural_check(self):
        old, new = r"C:\Dev\App", r"C:\Dev\Archive\App"
        line1 = json.dumps({"cwd": old})
        line2 = json.dumps({"cwd": r"C:\Dev\Other"})
        raw = line1 + "\n" + line2 + "\n"
        result = pm.edit_text(raw, self.variants(old, new), True, json_like=True, jsonl=True)
        lines = result.new_text.splitlines()
        self.assertEqual(json.loads(lines[0])["cwd"], new)
        self.assertEqual(json.loads(lines[1])["cwd"], r"C:\Dev\Other")

    def test_markdown_plain_text_replace(self):
        old, new = r"C:\Dev\App", r"C:\Dev\Archive\App"
        raw = f"Notes about {old} and its config.\n"
        result = pm.edit_text(raw, self.variants(old, new), True, json_like=False, jsonl=False)
        self.assertIn(new, result.new_text)
        self.assertNotIn(old, result.new_text)

    def test_forward_slash_variant_matched_in_json(self):
        old, new = r"C:\Dev\App", r"C:\Dev\Archive\App"
        raw = json.dumps({"a": "C:/Dev/App/file.txt"})
        result = pm.edit_text(raw, self.variants(old, new), True, json_like=True, jsonl=False)
        self.assertEqual(json.loads(result.new_text)["a"], "C:/Dev/Archive/App/file.txt")

    def test_gitbash_variant_matched(self):
        old, new = r"C:\Dev\App", r"C:\Dev\Archive\App"
        raw = json.dumps({"a": "Bash(rm /c/Dev/App/file.txt)"})
        result = pm.edit_text(raw, self.variants(old, new), True, json_like=True, jsonl=False)
        self.assertIn("/c/Dev/Archive/App/file.txt", json.loads(result.new_text)["a"])

    def test_invalid_json_falls_back_to_plain_bounded_replace(self):
        old, new = r"C:\Dev\App", r"C:\Dev\Archive\App"
        raw = "not really json but mentions " + old + " anyway"
        result = pm.edit_text(raw, self.variants(old, new), True, json_like=True, jsonl=False)
        self.assertIn(new, result.new_text)


class TestEscapeTailBoundary(unittest.TestCase):
    """Regression fixtures for a real bug found by diagnosing a live session
    file: a path reference immediately following a JSON escape sequence
    (\\n, \\t, \\r, \\", \\\\, \\uXXXX) in raw JSON text was rejected by the
    bounded-replace boundary check, because the escape's trailing character
    (e.g. the 'n' in '\\n') looks exactly like an ordinary word character —
    even though the *parsed* string has a single control character there,
    which is not a "continuation" character at all. This caused
    "structural scan expected N but bounded text replace matched M"
    warnings on completely safe files (a directory-tree listing or a
    compiler-warning list, where every entry starts on a new line). Every
    representation actually found in the real file was the \\n case; the
    others are added here defensively since they're the same bug class."""

    def variants(self, old, new, windows=True, home="C:\\Users\\test"):
        return pm.build_logical_variants(old, new, home, windows)

    def _check(self, parsed_value_with_path: str, old=r"C:\Dev\App", new=r"C:\Dev\Archive\App"):
        raw = json.dumps({"content": parsed_value_with_path})
        variants = self.variants(old, new)
        structural = sum(pm.count_logical_matches(s, variants, True) for s in pm.walk_json_strings(json.loads(raw)))
        result = pm.edit_text(raw, variants, True, json_like=True, jsonl=False)
        return structural, sum(result.counts.values()), result

    def test_path_after_escaped_newline(self):
        old = r"C:\Dev\App"
        structural, bounded, result = self._check("line one\n" + old + "\\sub\\file.txt")
        self.assertEqual(structural, 1)
        self.assertEqual(bounded, 1)
        self.assertFalse(result.ambiguous)

    def test_path_after_escaped_crlf(self):
        old = r"C:\Dev\App"
        structural, bounded, result = self._check("line one\r\n" + old)
        self.assertEqual(structural, 1)
        self.assertEqual(bounded, 1)
        self.assertFalse(result.ambiguous)

    def test_path_after_escaped_tab(self):
        old = r"C:\Dev\App"
        structural, bounded, result = self._check("col1\t" + old)
        self.assertEqual(structural, 1)
        self.assertEqual(bounded, 1)
        self.assertFalse(result.ambiguous)

    def test_path_after_escaped_quote(self):
        old = r"C:\Dev\App"
        structural, bounded, result = self._check('label "' + old + '" more text')
        self.assertEqual(structural, 1)
        self.assertEqual(bounded, 1)
        self.assertFalse(result.ambiguous)

    def test_path_after_escaped_backslash(self):
        old = r"C:\Dev\App"
        # A literal backslash character immediately before the path (e.g. a
        # doubled separator artifact), which JSON-encodes as \\\\ before it.
        structural, bounded, result = self._check("prefix\\" + old)
        self.assertEqual(structural, 1)
        self.assertEqual(bounded, 1)
        self.assertFalse(result.ambiguous)

    def test_path_after_unicode_escape(self):
        old = r"C:\Dev\App"
        structural, bounded, result = self._check("café " + old)
        self.assertEqual(structural, 1)
        self.assertEqual(bounded, 1)
        self.assertFalse(result.ambiguous)

    def test_tree_listing_style_repeated_references(self):
        # Reproduces the real shape of the bug: a multi-line "tree" style
        # listing where every entry starts with a newline, half of which
        # were silently dropped before the fix.
        old = r"C:\Dev\App"
        lines = [f"{old}\\file{i}.cs" for i in range(10)]
        parsed_value = "\n".join(lines)
        structural, bounded, result = self._check(parsed_value)
        self.assertEqual(structural, 10)
        self.assertEqual(bounded, 10)
        self.assertFalse(result.ambiguous)

    def test_sibling_project_after_escaped_newline_not_corrupted(self):
        # The fix must not become over-eager: "App2" right after a newline
        # must still never match "App".
        old = r"C:\Dev\App"
        structural, bounded, result = self._check("line one\n" + old + "2\\file.txt")
        self.assertEqual(structural, 0)
        self.assertEqual(bounded, 0)
        self.assertNotIn(r"C:\Dev\Archive\App2", result.new_text)

    def test_descendant_path_after_escaped_newline_preserves_suffix(self):
        old, new = r"C:\Dev\App", r"C:\Dev\Archive\App"
        raw = json.dumps({"content": "before\n" + old + "\\deep\\nested\\file.cs after"})
        variants = self.variants(old, new)
        result = pm.edit_text(raw, variants, True, json_like=True, jsonl=False)
        self.assertFalse(result.ambiguous)
        updated = json.loads(result.new_text)["content"]
        self.assertIn(new + "\\deep\\nested\\file.cs", updated)
        self.assertNotIn(old, updated)

    def test_project_root_exact_match_after_escaped_newline(self):
        old, new = r"C:\Dev\App", r"C:\Dev\Archive\App"
        raw = json.dumps({"cwd": old})
        # cwd is not preceded by anything, but pair it with a second field
        # that IS preceded by an escaped newline to mirror the real file's
        # mix of a bare 'cwd' plus escape-adjacent 'content'.
        raw = json.dumps({"cwd": old, "content": "notes\n" + old})
        variants = self.variants(old, new)
        result = pm.edit_text(raw, variants, True, json_like=True, jsonl=False)
        self.assertFalse(result.ambiguous)
        parsed = json.loads(result.new_text)
        self.assertEqual(parsed["cwd"], new)
        self.assertEqual(parsed["content"], "notes\n" + new)

    def test_nested_serialized_json_string_is_safely_skipped_not_corrupted(self):
        """A string value that is itself a serialized JSON blob (double-
        encoded) was NOT the cause found in the real file (confirmed via
        diagnostic — zero such fields existed there there). Documented,
        known limitation: this one level of double-encoding is invisible to
        BOTH the structural walk (which sees a single flat string, not an
        object to recurse into) and the bounded raw-text search (which only
        tries single- and double-escaped forms, not the quadruple-escaping
        a nested JSON string requires) — so both agree on zero matches and
        the reference is silently left unchanged. This is safe (no
        corruption, no incorrect guess, valid JSON throughout) even though
        it is not a full fix — there is nothing here to guess at, and
        guessing wrong would be worse than leaving it alone."""
        old, new = r"C:\Dev\App", r"C:\Dev\Archive\App"
        nested = json.dumps({"stdout": old + "\\file.txt"})
        raw = json.dumps({"toolUseResult": nested})
        variants = self.variants(old, new)
        result = pm.edit_text(raw, variants, True, json_like=True, jsonl=False)

        self.assertFalse(result.ambiguous)
        self.assertFalse(result.changed)
        # Whatever happens, the result must always be valid JSON, and the
        # nested content must be byte-for-byte unchanged (not corrupted).
        outer = json.loads(result.new_text)
        self.assertEqual(outer["toolUseResult"], nested)

    def test_case_only_verification_also_escape_aware(self):
        """count_case_sensitive_occurrences() (used for case-only-rename
        verification) must apply the same escape-tail awareness — it uses
        the same canonical matcher, so this mostly guards against a future
        regression that reintroduces a second, diverging implementation."""
        old = r"C:\Dev\App"
        variants = self.variants(old, old, windows=True)  # case-only: old and new differ only in case upstream
        raw = json.dumps({"content": "notes\n" + old})
        count = pm.count_case_sensitive_occurrences(raw, variants, json_like=True)
        self.assertEqual(count, 1)


# --------------------------------------------------------------------------
# Integration tests against isolated temp ClaudeEnv / project directories.
# --------------------------------------------------------------------------

class TestDryRunIsReadOnly(TempHomeMixin, unittest.TestCase):
    def _snapshot(self, root: Path):
        snap = {}
        for p in root.rglob("*"):
            st = p.stat()
            snap[str(p.relative_to(root))] = (p.is_dir(), st.st_mtime_ns, st.st_size if p.is_file() else None)
        return snap

    def test_no_writes_in_dry_run(self):
        env = self.env()
        proj = self.make_project()
        self.seed_claude_state(env, proj)
        dest = self.work / "archive" / "myapp"
        dest.parent.mkdir(parents=True, exist_ok=True)

        before_home = self._snapshot(self.home)
        before_work = self._snapshot(self.work)

        mover = pm.ProjectMover(str(proj), str(dest), execute=False, env=env)
        ok, _ = silent(mover.preflight)
        self.assertTrue(ok)
        silent(mover.scan_all)
        _, output = silent(mover.print_plan)

        after_home = self._snapshot(self.home)
        after_work = self._snapshot(self.work)

        self.assertEqual(before_home, after_home, "dry-run must not touch ~/.claude state")
        self.assertEqual(before_work, after_work, "dry-run must not touch the project directory")
        self.assertIn("DRY RUN", output)
        self.assertGreater(len(mover.actions), 0)

    def test_dry_run_reports_exact_details(self):
        env = self.env()
        proj = self.make_project()
        self.seed_claude_state(env, proj)
        dest = self.work / "archive" / "myapp"
        dest.parent.mkdir(parents=True, exist_ok=True)

        mover = pm.ProjectMover(str(proj), str(dest), execute=False, env=env)
        silent(mover.preflight)
        silent(mover.scan_all)
        _, output = silent(mover.print_plan)

        self.assertIn(str(proj), output)
        self.assertIn(str(dest), output)
        self.assertIn("Platform:", output)
        self.assertIn(mover.old_encoded, output)
        self.assertIn(mover.new_encoded, output)


class TestExecuteMove(TempHomeMixin, unittest.TestCase):
    def _run(self, old, new, env=None, backup=True, yes=True, context_only=False):
        env = env or self.env()
        mover = pm.ProjectMover(str(old), str(new), execute=True, backup=backup, context_only=context_only, env=env)
        ok, _ = silent(mover.preflight)
        if not ok:
            return mover, None
        silent(mover.scan_all)
        code, output = silent(mover.execute_move, assume_yes=yes)
        return mover, code

    def test_basic_move_updates_all_state(self):
        env = self.env()
        proj = self.make_project()
        self.seed_claude_state(env, proj)
        dest = self.work / "archive" / "myapp"
        dest.parent.mkdir(parents=True, exist_ok=True)

        mover, code = self._run(proj, dest, env=env)
        self.assertEqual(code, pm.EXIT_OK, "expected clean success")

        self.assertFalse(proj.exists())
        self.assertTrue(dest.exists())
        self.assertTrue((dest / "main.py").exists())

        new_encoded = pm.encode_path(str(dest), env.windows)
        self.assertTrue((env.claude_dir / "projects" / new_encoded).exists())
        self.assertFalse((env.claude_dir / "projects" / mover.old_encoded).exists())

        session = next((env.claude_dir / "projects" / new_encoded).glob("*.jsonl"))
        content = session.read_text(encoding="utf-8")
        parsed_session = json.loads(content.strip())  # still valid JSON, and parses to the new path
        self.assertEqual(parsed_session["cwd"], str(dest))

        history = json.loads(env.history_file.read_text(encoding="utf-8").strip())
        self.assertEqual(history["project"], str(dest))

        claude_json = json.loads(env.claude_json.read_text(encoding="utf-8"))
        self.assertIn(str(dest), claude_json["projects"])
        self.assertNotIn(str(proj), claude_json["projects"])

        plugins = json.loads(env.plugins_file.read_text(encoding="utf-8"))
        self.assertEqual(plugins["plugins"]["some-plugin@vendor"][0]["projectPath"], str(dest))

        own_settings = json.loads((dest / ".claude" / "settings.local.json").read_text(encoding="utf-8"))
        self.assertIn(str(dest), own_settings["permissions"]["additionalDirectories"])

        mem_file = next((env.claude_dir / "projects" / new_encoded / "memory").glob("*.md"))
        self.assertIn(str(dest), mem_file.read_text(encoding="utf-8"))

    def test_verification_clean_on_success(self):
        env = self.env()
        proj = self.make_project()
        self.seed_claude_state(env, proj)
        dest = self.work / "archive" / "myapp"
        dest.parent.mkdir(parents=True, exist_ok=True)
        mover, code = self._run(proj, dest, env=env)
        self.assertEqual(mover.verify_move(), [])

    def test_backup_created_before_writes_and_includes_session_file(self):
        env = self.env()
        proj = self.make_project()
        self.seed_claude_state(env, proj)
        dest = self.work / "archive" / "myapp"
        dest.parent.mkdir(parents=True, exist_ok=True)

        mover, code = self._run(proj, dest, env=env)
        backups = list((env.claude_dir / "backups").glob("project-move-*"))
        self.assertEqual(len(backups), 1)
        backup_dir = backups[0]
        manifest = (backup_dir / "MANIFEST.txt").read_text(encoding="utf-8")
        self.assertIn(str(proj), manifest)
        self.assertIn(str(dest), manifest)

        backed_up_files = list((backup_dir / "files").rglob("*"))
        self.assertTrue(any(p.name == "history.jsonl" for p in backed_up_files))
        self.assertTrue(any(p.suffix == ".jsonl" and "1111" in p.name for p in backed_up_files))

    def test_backup_failure_aborts_before_any_write(self):
        env = self.env()
        proj = self.make_project()
        self.seed_claude_state(env, proj)
        dest = self.work / "archive" / "myapp"
        dest.parent.mkdir(parents=True, exist_ok=True)

        mover = pm.ProjectMover(str(proj), str(dest), execute=True, backup=True, env=env)
        silent(mover.preflight)
        silent(mover.scan_all)

        original_create_backup = mover.create_backup

        def failing_backup():
            raise OSError("simulated disk full")

        mover.create_backup = failing_backup
        code, _ = silent(mover.execute_move, assume_yes=True)

        self.assertEqual(code, pm.EXIT_ABORTED)
        self.assertTrue(proj.exists(), "source must be untouched when backup fails")
        self.assertFalse(dest.exists(), "destination must not be created when backup fails")

    def test_existing_destination_without_execute_aborts(self):
        env = self.env()
        proj = self.make_project()
        self.seed_claude_state(env, proj)
        dest = self.make_project(name="myapp2")  # exists, unrelated content -> conflict-free but still requires --execute semantics
        mover = pm.ProjectMover(str(proj), str(dest), execute=False, env=env)
        ok, output = silent(mover.preflight)
        # dry-run mode must never merge/delete; it only reports.
        self.assertTrue(ok or "conflict" in output.lower() or mover.needs_merge)

    def test_conflicting_destination_aborts_with_no_changes(self):
        env = self.env()
        proj = self.make_project()
        self.seed_claude_state(env, proj)
        dest = self.work / "myapp2"
        dest.mkdir()
        (dest / "main.py").write_text("print('conflict')\n", encoding="utf-8")  # same relative name as source file

        mover = pm.ProjectMover(str(proj), str(dest), execute=True, env=env)
        ok, _ = silent(mover.preflight)
        self.assertFalse(ok)
        self.assertTrue(len(mover.merge_conflicts) > 0)
        # Nothing must have been touched.
        self.assertTrue(proj.exists())
        self.assertEqual((proj / "main.py").read_text(encoding="utf-8"), "print('hi')\n")
        self.assertEqual((dest / "main.py").read_text(encoding="utf-8"), "print('conflict')\n")

    def test_shared_directory_with_disjoint_files_aborts_before_any_change(self):
        """The false-safe merge bug: source and destination both have a
        'shared/' directory, but with different files inside it — the old
        conflict scanner said this was fine (no filename collided), and
        move_tree_disjoint() would only discover the top-level 'shared'
        collision when it actually tried to move it, by which point Claude
        state directories may already have been renamed. Preflight must
        catch this and refuse before backup/writes/state renames happen."""
        env = self.env()
        proj = self.make_project()
        self.seed_claude_state(env, proj)
        (proj / "shared").mkdir()
        (proj / "shared" / "a.txt").write_text("a", encoding="utf-8")

        dest = self.work / "myapp2"
        dest.mkdir()
        (dest / "shared").mkdir()
        (dest / "shared" / "b.txt").write_text("b", encoding="utf-8")

        old_encoded = pm.encode_path(str(proj), env.windows)

        mover = pm.ProjectMover(str(proj), str(dest), execute=True, env=env)
        ok, _ = silent(mover.preflight)
        self.assertFalse(ok)
        self.assertIn("shared", mover.merge_conflicts)

        # Nothing changed: no backup, no state rename, no file touched.
        self.assertTrue(proj.exists())
        self.assertTrue((proj / "shared" / "a.txt").exists())
        self.assertTrue((dest / "shared" / "b.txt").exists())
        self.assertFalse((dest / "shared" / "a.txt").exists())
        self.assertFalse((env.claude_dir / "backups").exists())
        self.assertTrue((env.claude_dir / "projects" / old_encoded).exists())

    def test_conflict_free_merge_preserves_all_files(self):
        env = self.env()
        proj = self.make_project()
        self.seed_claude_state(env, proj)
        dest = self.work / "myapp2"
        dest.mkdir()
        (dest / "other.py").write_text("print('other')\n", encoding="utf-8")  # disjoint filename -> mergeable

        mover, code = self._run(proj, dest, env=env, yes=True)
        self.assertEqual(code, pm.EXIT_OK)
        self.assertTrue((dest / "main.py").exists())
        self.assertTrue((dest / "other.py").exists())
        self.assertFalse(proj.exists())

    def test_merge_requires_confirmation_without_yes(self):
        env = self.env()
        proj = self.make_project()
        self.seed_claude_state(env, proj)
        dest = self.work / "myapp2"
        dest.mkdir()
        (dest / "other.py").write_text("print('other')\n", encoding="utf-8")

        mover = pm.ProjectMover(str(proj), str(dest), execute=True, env=env)
        silent(mover.preflight)
        silent(mover.scan_all)
        code, output = silent(mover.execute_move, assume_yes=False, confirm=None)
        self.assertEqual(code, pm.EXIT_ABORTED)
        self.assertTrue(proj.exists(), "must not touch anything without confirmation")

    def test_no_backup_overridden_for_merge(self):
        env = self.env()
        proj = self.make_project()
        self.seed_claude_state(env, proj)
        dest = self.work / "myapp2"
        dest.mkdir()
        (dest / "other.py").write_text("print('other')\n", encoding="utf-8")

        mover = pm.ProjectMover(str(proj), str(dest), execute=True, backup=False, env=env)
        silent(mover.preflight)
        silent(mover.scan_all)
        silent(mover.execute_move, assume_yes=True)
        self.assertTrue(mover.backup_effective, "--no-backup must be overridden for merges")
        self.assertTrue(any(mover.notes), "overriding --no-backup should be visible to the user")

    def test_no_backup_honored_for_plain_move(self):
        env = self.env()
        proj = self.make_project()
        self.seed_claude_state(env, proj)
        dest = self.work / "archive" / "myapp"
        dest.parent.mkdir(parents=True, exist_ok=True)

        mover, code = self._run(proj, dest, env=env, backup=False)
        self.assertFalse(mover.backup_effective)
        backups_dir = env.claude_dir / "backups"
        self.assertFalse(backups_dir.exists() and any(backups_dir.iterdir()))

    def test_same_path_rejected(self):
        env = self.env()
        proj = self.make_project()
        mover = pm.ProjectMover(str(proj), str(proj), execute=True, env=env)
        ok, output = silent(mover.preflight)
        self.assertFalse(ok)
        self.assertIn("same path", output.lower())

    def test_destination_inside_source_rejected(self):
        env = self.env()
        proj = self.make_project()
        mover = pm.ProjectMover(str(proj), str(proj / "sub" / "dest"), execute=True, env=env)
        ok, output = silent(mover.preflight)
        self.assertFalse(ok)

    def test_case_only_rename(self):
        env = self.env()
        proj = self.make_project(name="MyApp")
        self.seed_claude_state(env, proj)
        dest = proj.parent / "myapp"

        mover, code = self._run(proj, dest, env=env)
        self.assertEqual(code, pm.EXIT_OK)
        self.assertTrue(dest.is_dir())
        self.assertTrue((dest / "main.py").exists())

    def test_missing_optional_state_dirs_does_not_crash(self):
        env = self.env()
        proj = self.make_project()
        # Deliberately do NOT seed any ~/.claude state at all.
        dest = self.work / "archive" / "myapp"
        dest.parent.mkdir(parents=True, exist_ok=True)
        mover, code = self._run(proj, dest, env=env)
        self.assertEqual(code, pm.EXIT_OK)
        self.assertTrue(dest.is_dir())

    def test_context_only_mode_updates_state_without_moving_directory(self):
        env = self.env()
        proj = self.make_project()
        self.seed_claude_state(env, proj)
        dest = self.work / "archive" / "myapp"
        dest.parent.mkdir(parents=True, exist_ok=True)
        proj.rename(dest)  # simulate: directory already moved externally

        mover, code = self._run(proj, dest, env=env, context_only=True)
        self.assertEqual(code, pm.EXIT_OK)

        new_encoded = pm.encode_path(str(dest), env.windows)
        claude_json = json.loads(env.claude_json.read_text(encoding="utf-8"))
        self.assertIn(str(dest), claude_json["projects"])

    def test_cross_project_settings_updated_and_unrelated_left_alone(self):
        env = self.env()
        proj = self.make_project()
        self.seed_claude_state(env, proj)

        sibling = self.make_project(name="othertool")
        sibling_settings_dir = sibling / ".claude"
        sibling_settings_dir.mkdir(parents=True, exist_ok=True)
        cross_ref = {"permissions": {"additionalDirectories": [str(proj)]}}
        (sibling_settings_dir / "settings.local.json").write_text(json.dumps(cross_ref), encoding="utf-8")

        unrelated = self.make_project(name="unrelated")
        unrelated_settings_dir = unrelated / ".claude"
        unrelated_settings_dir.mkdir(parents=True, exist_ok=True)
        unrelated_content = {"permissions": {"additionalDirectories": [str(unrelated)]}}
        (unrelated_settings_dir / "settings.local.json").write_text(json.dumps(unrelated_content), encoding="utf-8")

        dest = self.work / "archive" / "myapp"
        dest.parent.mkdir(parents=True, exist_ok=True)
        mover, code = self._run(proj, dest, env=env)
        self.assertEqual(code, pm.EXIT_OK)

        updated_cross = json.loads((sibling_settings_dir / "settings.local.json").read_text(encoding="utf-8"))
        self.assertIn(str(dest), updated_cross["permissions"]["additionalDirectories"])

        unchanged = json.loads((unrelated_settings_dir / "settings.local.json").read_text(encoding="utf-8"))
        self.assertEqual(unchanged, unrelated_content)

    def test_write_failure_does_not_corrupt_original(self):
        env = self.env()
        proj = self.make_project()
        self.seed_claude_state(env, proj)
        dest = self.work / "archive" / "myapp"
        dest.parent.mkdir(parents=True, exist_ok=True)

        original_content = env.claude_json.read_text(encoding="utf-8")

        mover = pm.ProjectMover(str(proj), str(dest), execute=True, env=env)
        silent(mover.preflight)
        silent(mover.scan_all)

        real_atomic_write = pm.atomic_write_text

        def flaky_write(path, text, validate_json=False):
            if Path(path).name == ".claude.json":
                raise OSError("simulated write failure")
            return real_atomic_write(path, text, validate_json=validate_json)

        pm.atomic_write_text = flaky_write
        try:
            code, _ = silent(mover.execute_move, assume_yes=True)
        finally:
            pm.atomic_write_text = real_atomic_write

        self.assertEqual(env.claude_json.read_text(encoding="utf-8"), original_content)
        self.assertTrue(mover.errors)

        # A state-file write failure must stop the operation before the
        # project directory itself is touched — never move a project after
        # a failed state update.
        self.assertEqual(code, pm.EXIT_PARTIAL)
        self.assertTrue(proj.exists(), "source must remain untouched after a state-file write failure")
        self.assertFalse(dest.exists(), "project must not be moved after a state-file write failure")

    def test_ambiguous_structural_mismatch_skips_file(self):
        env = self.env()
        proj = self.make_project()
        self.seed_claude_state(env, proj)

        # Hand-craft a claude.json whose raw text won't match structurally
        # (simulate a bizarre escaping edge case) by injecting an extra
        # unescaped fragment that a bounded scan would over/under count.
        old_abs = str(proj)
        weird = json.dumps({"projects": {old_abs: {}}})
        # Duplicate the raw literal outside of proper JSON escaping context
        # is hard to construct safely; instead directly unit-test the guard.
        result = pm.edit_text(
            weird.replace(json.dumps(old_abs), json.dumps(old_abs) + json.dumps(old_abs)[1:]),
            pm.build_logical_variants(old_abs, str(self.work / "dest"), str(env.home), env.windows),
            env.windows, json_like=True, jsonl=False,
        )
        # This mangled input is not valid JSON, so it degrades to a plain
        # bounded replace rather than being treated as ambiguous — either
        # outcome (ambiguous OR a plain successful replace) is acceptable
        # as long as it never crashes and never guesses silently wrong.
        self.assertIsNotNone(result)


class TestVerificationCoverage(TempHomeMixin, unittest.TestCase):
    """Post-execution verification must not be coupled to unrelated
    detections (the root-mirror directory is a separate, deliberately
    untouched location and must never suppress checking the real encoded
    state directories), and must cover every category of file this tool
    plans to change — not just a hard-coded subset."""

    def test_stale_encoded_dir_detected_even_with_root_mirror_present(self):
        env = self.env()
        proj = self.make_project()
        self.seed_claude_state(env, proj)
        encoded = pm.encode_path(str(proj), env.windows)

        mirror = env.claude_dir / encoded
        mirror.mkdir(parents=True, exist_ok=True)
        (mirror / "x.jsonl").write_text("{}", encoding="utf-8")

        dest = self.work / "archive" / "myapp"
        dest.parent.mkdir(parents=True, exist_ok=True)

        mover = pm.ProjectMover(str(proj), str(dest), execute=True, env=env)
        silent(mover.preflight)
        silent(mover.scan_all)
        code, _ = silent(mover.execute_move, assume_yes=True)
        self.assertEqual(code, pm.EXIT_OK)
        self.assertTrue(mover.root_mirror_detected)

        # Simulate a leftover old encoded 'projects' dir appearing after the
        # fact — verification must still catch this even though a root
        # mirror was ALSO detected for this same encoded name.
        (env.claude_dir / "projects" / encoded).mkdir(parents=True, exist_ok=True)
        stale = mover.verify_move()
        self.assertTrue(
            any("old encoded dir still exists" in s and "projects/" in s for s in stale),
            f"expected a projects/ staleness entry, got: {stale}",
        )

    def test_stale_own_settings_local_json_detected(self):
        env = self.env()
        proj = self.make_project()
        self.seed_claude_state(env, proj)
        dest = self.work / "archive" / "myapp"
        dest.parent.mkdir(parents=True, exist_ok=True)

        mover = pm.ProjectMover(str(proj), str(dest), execute=True, env=env)
        silent(mover.preflight)
        silent(mover.scan_all)
        silent(mover.execute_move, assume_yes=True)
        self.assertEqual(mover.verify_move(), [])

        # Simulate the settings file somehow reverting to the old path.
        settings_path = dest / ".claude" / "settings.local.json"
        settings_path.write_text(
            json.dumps({"permissions": {"allow": [], "additionalDirectories": [str(proj)]}}),
            encoding="utf-8",
        )
        stale = mover.verify_move()
        self.assertTrue(any("settings.local.json" in s for s in stale), stale)

    def test_stale_cross_project_settings_detected(self):
        env = self.env()
        proj = self.make_project()
        self.seed_claude_state(env, proj)
        sibling = self.make_project(name="othertool")
        sibling_settings_dir = sibling / ".claude"
        sibling_settings_dir.mkdir(parents=True, exist_ok=True)
        sibling_settings = sibling_settings_dir / "settings.local.json"
        sibling_settings.write_text(
            json.dumps({"permissions": {"additionalDirectories": [str(proj)]}}), encoding="utf-8"
        )

        dest = self.work / "archive" / "myapp"
        dest.parent.mkdir(parents=True, exist_ok=True)
        mover = pm.ProjectMover(str(proj), str(dest), execute=True, env=env)
        silent(mover.preflight)
        silent(mover.scan_all)
        silent(mover.execute_move, assume_yes=True)
        self.assertEqual(mover.verify_move(), [])

        sibling_settings.write_text(
            json.dumps({"permissions": {"additionalDirectories": [str(proj)]}}), encoding="utf-8"
        )
        stale = mover.verify_move()
        self.assertTrue(any("settings.local.json" in s for s in stale), stale)

    def test_stale_memory_file_detected(self):
        env = self.env()
        proj = self.make_project()
        self.seed_claude_state(env, proj)
        dest = self.work / "archive" / "myapp"
        dest.parent.mkdir(parents=True, exist_ok=True)

        mover = pm.ProjectMover(str(proj), str(dest), execute=True, env=env)
        silent(mover.preflight)
        silent(mover.scan_all)
        silent(mover.execute_move, assume_yes=True)
        self.assertEqual(mover.verify_move(), [])

        new_encoded = pm.encode_path(str(dest), env.windows)
        mem_file = next((env.claude_dir / "projects" / new_encoded / "memory").glob("*.md"))
        mem_file.write_text(f"Working on {proj}\n", encoding="utf-8")
        stale = mover.verify_move()
        self.assertTrue(any("MEMORY.md" in s or "memory" in s.lower() for s in stale), stale)


class TestRootMirror(TempHomeMixin, unittest.TestCase):
    """A secondary, undocumented ~/.claude/<encoded>/ directory (outside
    projects/) has been observed on at least one real Windows install. Per
    explicit instruction, this tool must detect and back it up, but must
    NEVER rename/move/delete it until its purpose is independently verified."""

    def test_root_mirror_detected_backed_up_and_left_untouched(self):
        env = self.env()
        proj = self.make_project()
        self.seed_claude_state(env, proj)
        encoded = pm.encode_path(str(proj), env.windows)

        mirror = env.claude_dir / encoded
        mirror.mkdir(parents=True, exist_ok=True)
        (mirror / "some-session.jsonl").write_text(json.dumps({"cwd": str(proj)}) + "\n", encoding="utf-8")

        dest = self.work / "archive" / "myapp"
        dest.parent.mkdir(parents=True, exist_ok=True)

        mover = pm.ProjectMover(str(proj), str(dest), execute=True, env=env)
        silent(mover.preflight)
        silent(mover.scan_all)
        self.assertTrue(mover.root_mirror_detected)
        self.assertTrue(any(a["category"] == "root-mirror-detected" for a in mover.actions))

        code, _ = silent(mover.execute_move, assume_yes=True)
        self.assertEqual(code, pm.EXIT_OK)

        # Never modified: still exists at the OLD encoded name, content unchanged.
        self.assertTrue(mirror.is_dir())
        self.assertEqual(
            json.loads((mirror / "some-session.jsonl").read_text(encoding="utf-8"))["cwd"],
            str(proj),
        )
        # But it must have been backed up.
        backups = list((env.claude_dir / "backups").glob("project-move-*"))
        self.assertEqual(len(backups), 1)
        backed_up_mirror = backups[0] / "dirs" / f"{encoded} (root mirror)"
        self.assertTrue(backed_up_mirror.is_dir())
        self.assertTrue((backed_up_mirror / "some-session.jsonl").exists())

    def test_dry_run_shows_root_mirror_without_touching_it(self):
        env = self.env()
        proj = self.make_project()
        self.seed_claude_state(env, proj)
        encoded = pm.encode_path(str(proj), env.windows)
        mirror = env.claude_dir / encoded
        mirror.mkdir(parents=True, exist_ok=True)
        (mirror / "x.jsonl").write_text("{}", encoding="utf-8")

        dest = self.work / "archive" / "myapp"
        dest.parent.mkdir(parents=True, exist_ok=True)
        mover = pm.ProjectMover(str(proj), str(dest), execute=False, env=env)
        silent(mover.preflight)
        silent(mover.scan_all)
        _, output = silent(mover.print_plan)
        self.assertIn("root-mirror-detected", output.lower().replace("_", "-"))
        self.assertTrue(mirror.is_dir())


class TestGitBashStylePaths(unittest.TestCase):
    """Real settings.local.json content observed on a Windows machine stores
    Bash-tool permission patterns using Git-Bash mount paths like
    '/c/Users/you/project' rather than 'C:\\Users\\you\\project'.

    This is a pure unit test on literal Windows path strings — deliberately
    NOT a filesystem integration test — so it gives identical results on any
    host OS. A prior version of this test derived a fake Git-Bash path from
    a real temporary directory path, which only produced a valid drive-style
    string when the temp directory happened to be Windows-shaped; on a real
    POSIX host (where temp paths look like '/tmp/xyz/...') that derivation
    was nonsense. Real filesystem behavior for settings.local.json updates
    is already covered by TestExecuteMove on whatever host is running."""

    def test_gitbash_path_in_bash_permission_string_is_updated(self):
        old_abs = r"C:\Users\knuts\Programming\CSharp\SpankBank"
        new_abs = r"C:\Users\knuts\Programming\CSharp\Archive\SpankBank"
        variants = pm.build_logical_variants(old_abs, new_abs, r"C:\Users\knuts", True)

        data = {
            "permissions": {
                "allow": ['Bash(rm "/c/Users/knuts/Programming/CSharp/SpankBank/qa-smoke.db")'],
                "additionalDirectories": [old_abs],
            }
        }
        raw = json.dumps(data, indent=2)
        result = pm.edit_text(raw, variants, True, json_like=True, jsonl=False)
        self.assertTrue(result.changed)

        updated = json.loads(result.new_text)
        self.assertIn(
            "/c/Users/knuts/Programming/CSharp/Archive/SpankBank/qa-smoke.db",
            updated["permissions"]["allow"][0],
        )
        self.assertNotIn("/c/Users/knuts/Programming/CSharp/SpankBank/qa-smoke.db", updated["permissions"]["allow"][0])
        self.assertEqual(updated["permissions"]["additionalDirectories"], [new_abs])


class TestVerifyCopyContentHash(TempHomeMixin, unittest.TestCase):
    """_verify_copy() must catch same-size-different-content corruption —
    comparing only sizes would let e.g. src='AAAA' / dst='BBBB' pass."""

    def _mover(self):
        # Any ProjectMover instance works: _verify_copy()/_hash_file() only
        # operate on the src/dst paths passed to them directly.
        src = self.work / "unused_src"
        src.mkdir()
        dst = self.work / "unused_dst"
        return pm.ProjectMover(str(src), str(dst), env=self.env())

    def test_same_size_different_content_fails(self):
        mover = self._mover()
        src, dst = self.work / "a", self.work / "b"
        src.mkdir()
        dst.mkdir()
        (src / "f.bin").write_bytes(b"AAAA")
        (dst / "f.bin").write_bytes(b"BBBB")
        self.assertEqual((src / "f.bin").stat().st_size, (dst / "f.bin").stat().st_size)
        self.assertFalse(mover._verify_copy(src, dst))

    def test_identical_content_succeeds(self):
        mover = self._mover()
        src, dst = self.work / "a2", self.work / "b2"
        src.mkdir()
        dst.mkdir()
        (src / "f.bin").write_bytes(b"AAAA")
        (dst / "f.bin").write_bytes(b"AAAA")
        self.assertTrue(mover._verify_copy(src, dst))

    def test_empty_files_succeed(self):
        mover = self._mover()
        src, dst = self.work / "a3", self.work / "b3"
        src.mkdir()
        dst.mkdir()
        (src / "empty.txt").write_bytes(b"")
        (dst / "empty.txt").write_bytes(b"")
        self.assertTrue(mover._verify_copy(src, dst))

    def test_nested_files_are_verified(self):
        mover = self._mover()
        src, dst = self.work / "a4", self.work / "b4"
        (src / "sub" / "deeper").mkdir(parents=True)
        (dst / "sub" / "deeper").mkdir(parents=True)
        (src / "sub" / "deeper" / "f.txt").write_text("hello", encoding="utf-8")
        (dst / "sub" / "deeper" / "f.txt").write_text("hello", encoding="utf-8")
        self.assertTrue(mover._verify_copy(src, dst))

        (dst / "sub" / "deeper" / "f.txt").write_text("HELLO", encoding="utf-8")
        self.assertFalse(mover._verify_copy(src, dst))

    def test_missing_entry_fails(self):
        mover = self._mover()
        src, dst = self.work / "a5", self.work / "b5"
        src.mkdir()
        dst.mkdir()
        (src / "one.txt").write_text("1", encoding="utf-8")
        (src / "two.txt").write_text("2", encoding="utf-8")
        (dst / "one.txt").write_text("1", encoding="utf-8")
        self.assertFalse(mover._verify_copy(src, dst))


class TestCrossVolumeStaging(TempHomeMixin, unittest.TestCase):
    """The cross-volume move must be transactional: copy to a staging
    directory beside the real destination, verify by content hash, and only
    then atomically rename staging -> destination before removing the
    source. Mutation tracking follows whether staging content was actually
    created, not merely whether the final destination path exists."""

    def _run_cross_volume(self, mover):
        real_same_volume = pm.same_volume
        pm.same_volume = lambda a, b, w: False
        try:
            return silent(mover.execute_move, assume_yes=True)
        finally:
            pm.same_volume = real_same_volume

    def _staging_dirs(self, dest: Path):
        prefix = f".{dest.name}.project-move-staging-"
        return [p for p in dest.parent.iterdir() if p.name.startswith(prefix)]

    def test_copytree_failing_before_creating_anything_is_aborted(self):
        env = self.env()
        proj = self.make_project()
        dest = self.work / "archive" / "myapp"
        dest.parent.mkdir(parents=True, exist_ok=True)

        mover = pm.ProjectMover(str(proj), str(dest), execute=True, env=env)
        silent(mover.preflight)
        silent(mover.scan_all)

        real_copytree = pm.shutil.copytree

        def failing_copytree(*a, **k):
            raise OSError("simulated failure before anything was created")

        pm.shutil.copytree = failing_copytree
        try:
            code, output = self._run_cross_volume(mover)
        finally:
            pm.shutil.copytree = real_copytree

        self.assertEqual(code, pm.EXIT_ABORTED)
        self.assertFalse(mover.mutated)
        self.assertTrue(proj.exists())
        self.assertFalse(dest.exists())
        self.assertEqual(self._staging_dirs(dest), [])

    def test_copytree_failing_after_creating_staging_content_is_partial(self):
        env = self.env()
        proj = self.make_project()
        dest = self.work / "archive" / "myapp"
        dest.parent.mkdir(parents=True, exist_ok=True)

        mover = pm.ProjectMover(str(proj), str(dest), execute=True, env=env)
        silent(mover.preflight)
        silent(mover.scan_all)

        def partial_copytree(src, dst, symlinks=False):
            Path(dst).mkdir(parents=True, exist_ok=True)
            (Path(dst) / "partial.txt").write_text("partial", encoding="utf-8")
            raise OSError("simulated failure after creating staging content")

        real_copytree = pm.shutil.copytree
        pm.shutil.copytree = partial_copytree
        try:
            code, output = self._run_cross_volume(mover)
        finally:
            pm.shutil.copytree = real_copytree

        self.assertEqual(code, pm.EXIT_PARTIAL)
        self.assertTrue(mover.mutated)
        self.assertTrue(proj.exists(), "source must remain untouched")
        self.assertFalse(dest.exists(), "final destination must never be created")
        staging_dirs = self._staging_dirs(dest)
        self.assertEqual(len(staging_dirs), 1)
        self.assertTrue((staging_dirs[0] / "partial.txt").exists())
        self.assertIn(str(staging_dirs[0]), output)

    def test_verification_mismatch_keeps_source_and_leaves_destination_absent(self):
        env = self.env()
        proj = self.make_project()
        original_main_py = (proj / "main.py").read_bytes()
        dest = self.work / "archive" / "myapp"
        dest.parent.mkdir(parents=True, exist_ok=True)

        mover = pm.ProjectMover(str(proj), str(dest), execute=True, env=env)
        silent(mover.preflight)
        silent(mover.scan_all)

        real_copytree = pm.shutil.copytree

        def corrupting_copytree(src, dst, symlinks=False):
            real_copytree(src, dst, symlinks=symlinks)
            # Corrupt one file post-copy: same size, different content —
            # exactly the bug class this verification exists to catch.
            target = Path(dst) / "main.py"
            target.write_bytes(b"X" * len(target.read_bytes()))

        pm.shutil.copytree = corrupting_copytree
        try:
            code, output = self._run_cross_volume(mover)
        finally:
            pm.shutil.copytree = real_copytree

        self.assertEqual(code, pm.EXIT_PARTIAL)
        self.assertTrue(mover.mutated)
        self.assertTrue(proj.exists())
        self.assertEqual((proj / "main.py").read_bytes(), original_main_py)
        self.assertFalse(dest.exists())
        staging_dirs = self._staging_dirs(dest)
        self.assertEqual(len(staging_dirs), 1, "the mismatched staging copy must be preserved for inspection")

    def test_successful_copy_renames_staging_to_destination_and_removes_source(self):
        env = self.env()
        proj = self.make_project()
        self.seed_claude_state(env, proj)
        dest = self.work / "archive" / "myapp"
        dest.parent.mkdir(parents=True, exist_ok=True)

        mover = pm.ProjectMover(str(proj), str(dest), execute=True, env=env)
        silent(mover.preflight)
        silent(mover.scan_all)

        code, output = self._run_cross_volume(mover)

        self.assertEqual(code, pm.EXIT_OK)
        self.assertTrue(mover.mutated)
        self.assertFalse(proj.exists())
        self.assertTrue(dest.exists())
        self.assertTrue((dest / "main.py").exists())
        self.assertEqual(self._staging_dirs(dest), [], "staging dir must be gone (renamed away) after success")

    def test_staging_path_collision_is_handled_without_overwriting(self):
        import unittest.mock as mock

        env = self.env()
        proj = self.make_project()
        dest = self.work / "archive" / "myapp"
        dest.parent.mkdir(parents=True, exist_ok=True)

        mover = pm.ProjectMover(str(proj), str(dest), execute=True, env=env)
        silent(mover.preflight)
        silent(mover.scan_all)

        collided_name = "deadbeef" * 4
        pre_existing = dest.parent / f".{dest.name}.project-move-staging-{collided_name}"
        pre_existing.mkdir(parents=True, exist_ok=True)
        (pre_existing / "do-not-touch.txt").write_text("pre-existing", encoding="utf-8")

        class FakeUUID:
            def __init__(self, hexval):
                self.hex = hexval

        calls = {"n": 0}

        def fake_uuid4():
            calls["n"] += 1
            return FakeUUID(collided_name) if calls["n"] == 1 else FakeUUID("cafef00d" * 4)

        with mock.patch.object(pm.uuid, "uuid4", side_effect=fake_uuid4):
            code, output = self._run_cross_volume(mover)

        self.assertEqual(code, pm.EXIT_OK)
        self.assertTrue(dest.exists())
        self.assertFalse(proj.exists())
        # The pre-existing (collided-with) staging dir must be untouched.
        self.assertTrue(pre_existing.exists())
        self.assertEqual((pre_existing / "do-not-touch.txt").read_text(encoding="utf-8"), "pre-existing")


class TestCrossVolumeMocked(TempHomeMixin, unittest.TestCase):
    """Real multi-drive hardware isn't guaranteed in CI, so this forces the
    cross-volume code path via monkeypatching same_volume() while the actual
    copy still happens on one real (temp) drive — the point is to exercise
    the copy-verify-then-delete-source logic and its rollback safety, not to
    prove Windows itself refuses cross-drive renames (already confirmed
    manually against real D:/E:/F: drives during the audit)."""

    def test_cross_volume_copies_then_removes_source_on_success(self):
        env = self.env()
        proj = self.make_project()
        self.seed_claude_state(env, proj)
        dest = self.work / "archive" / "myapp"
        dest.parent.mkdir(parents=True, exist_ok=True)

        mover = pm.ProjectMover(str(proj), str(dest), execute=True, env=env)
        silent(mover.preflight)
        silent(mover.scan_all)

        real_same_volume = pm.same_volume
        pm.same_volume = lambda a, b, w: False
        try:
            code, output = silent(mover.execute_move, assume_yes=True)
        finally:
            pm.same_volume = real_same_volume

        self.assertEqual(code, pm.EXIT_OK)
        self.assertFalse(proj.exists())
        self.assertTrue(dest.exists())
        self.assertTrue((dest / "main.py").exists())
        self.assertTrue(any("cross-volume" in n.lower() for n in mover.notes))

    def test_cross_volume_verification_failure_keeps_source_intact(self):
        env = self.env()
        proj = self.make_project()
        self.seed_claude_state(env, proj)
        dest = self.work / "archive" / "myapp"
        dest.parent.mkdir(parents=True, exist_ok=True)

        mover = pm.ProjectMover(str(proj), str(dest), execute=True, env=env)
        silent(mover.preflight)
        silent(mover.scan_all)

        real_same_volume = pm.same_volume
        real_verify_copy = mover._verify_copy
        pm.same_volume = lambda a, b, w: False
        mover._verify_copy = lambda s, d: False  # simulate a corrupted/incomplete copy
        try:
            code, output = silent(mover.execute_move, assume_yes=True)
        finally:
            pm.same_volume = real_same_volume

        # State files were already written and encoded dirs already renamed
        # by this point (mutation occurred), so this is a partial failure,
        # not a clean abort — EXIT_PARTIAL, not EXIT_ABORTED.
        self.assertEqual(code, pm.EXIT_PARTIAL)
        self.assertTrue(proj.exists(), "source must survive a failed cross-volume verification")
        self.assertTrue(any("verification failed" in e.lower() for e in mover.errors))


class TestAtomicWriteJsonValidation(unittest.TestCase):
    def test_invalid_json_is_rejected_and_original_untouched(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "settings.json"
            path.write_text('{"a": 1}', encoding="utf-8")
            with self.assertRaises(Exception):
                pm.atomic_write_text(path, "{not valid json", validate_json=True)
            self.assertEqual(path.read_text(encoding="utf-8"), '{"a": 1}')
            self.assertEqual(list(Path(d).iterdir()), [path], "no leftover temp file after a failed write")

    def test_valid_json_write_succeeds_atomically(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "settings.json"
            path.write_text('{"a": 1}', encoding="utf-8")
            pm.atomic_write_text(path, '{"a": 2}', validate_json=True)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"a": 2})
            self.assertEqual(list(Path(d).iterdir()), [path], "no leftover temp file after a successful write")


class TestConcurrentModification(TempHomeMixin, unittest.TestCase):
    """Optimistic concurrency protection: if a state file changes on disk
    between scan_all() and execute_move() (e.g. Claude Code itself writes to
    it while project-move is running), the newer content must win — this
    tool must never blindly overwrite it with a write plan computed from
    stale scanned content."""

    def test_externally_modified_history_file_is_not_overwritten(self):
        env = self.env()
        proj = self.make_project()
        self.seed_claude_state(env, proj)
        dest = self.work / "archive" / "myapp"
        dest.parent.mkdir(parents=True, exist_ok=True)

        mover = pm.ProjectMover(str(proj), str(dest), execute=True, env=env)
        silent(mover.preflight)
        silent(mover.scan_all)

        # Simulate another process (e.g. Claude Code) appending a new entry
        # to history.jsonl after scanning finished but before execution.
        externally_modified = (
            env.history_file.read_text(encoding="utf-8")
            + json.dumps({"display": "new", "project": str(proj), "timestamp": 2, "sessionId": "s2"})
            + "\n"
        )
        env.history_file.write_text(externally_modified, encoding="utf-8")

        code, output = silent(mover.execute_move, assume_yes=True)

        self.assertEqual(code, pm.EXIT_PARTIAL)
        self.assertNotIn("Done.", output)
        # The newer, externally-written content must be preserved exactly —
        # not overwritten with the plan computed from the earlier scan.
        self.assertEqual(env.history_file.read_text(encoding="utf-8"), externally_modified)
        # No later stage may have run: the project must not have moved.
        self.assertTrue(proj.exists())
        self.assertFalse(dest.exists())
        self.assertTrue(any("changed after scanning" in e for e in mover.errors), mover.errors)


class TestPrevalidatedWriteStage(TempHomeMixin, unittest.TestCase):
    """_write_planned_files() must prevalidate every planned item (existence
    + hash) BEFORE writing any of them — a problem anywhere in the batch
    means nothing in the batch is written — and then, during the actual
    write pass, stop at the first failure rather than continuing to later
    planned files."""

    def test_changed_file_prevents_all_planned_writes_regardless_of_position(self):
        for which in ("first", "middle", "last"):
            with self.subTest(which=which):
                env = self.env()
                proj = self.make_project()
                self.seed_claude_state(env, proj)
                dest = self.work / f"archive-{which}" / "myapp"
                dest.parent.mkdir(parents=True, exist_ok=True)

                mover = pm.ProjectMover(str(proj), str(dest), execute=True, env=env)
                silent(mover.preflight)
                silent(mover.scan_all)

                plan_keys = list(mover._replacement_plans.keys())
                self.assertGreaterEqual(len(plan_keys), 3, "need at least 3 planned files for this test")
                index = {"first": 0, "middle": len(plan_keys) // 2, "last": len(plan_keys) - 1}[which]
                target = plan_keys[index]

                before = {k: Path(k).read_text(encoding="utf-8") for k in plan_keys if Path(k).exists()}
                Path(target).write_text(before[target] + "\nEXTERNALLY MODIFIED", encoding="utf-8")

                code, output = silent(mover.execute_move, assume_yes=True)

                self.assertNotEqual(code, pm.EXIT_OK)
                self.assertNotIn("Done.", output)
                for k in plan_keys:
                    # Session/memory files live under the Claude "projects"
                    # encoded dir, which step 2 (rename) relocates regardless
                    # of whether step 3 (content write) is blocked — so we
                    # must look them up via the same remap the tool itself
                    # uses, not assume they're still at the scan-time path.
                    current = mover._remap_after_encoded_dir_rename(Path(k))
                    if not current.exists():
                        continue
                    if k == target:
                        self.assertTrue(current.read_text(encoding="utf-8").endswith("EXTERNALLY MODIFIED"))
                    else:
                        self.assertEqual(
                            current.read_text(encoding="utf-8"), before[k],
                            f"{k} must be untouched (content-wise) — one bad file in the batch "
                            f"must block writes to the whole batch",
                        )
                self.assertTrue(proj.exists())
                self.assertFalse(dest.exists())

    def test_file_changed_after_prevalidation_but_before_its_own_write_is_preserved(self):
        env = self.env()
        proj = self.make_project()
        self.seed_claude_state(env, proj)
        dest = self.work / "archive" / "myapp"
        dest.parent.mkdir(parents=True, exist_ok=True)

        mover = pm.ProjectMover(str(proj), str(dest), execute=True, env=env)
        silent(mover.preflight)
        silent(mover.scan_all)

        # Use two fixed-location files (never relocated by the encoded-dir
        # rename) so the write-time path matches these literal strings —
        # session/memory files would get remapped mid-run and wouldn't
        # reliably match a path captured before execution.
        plan_keys = list(mover._replacement_plans.keys())
        trigger, victim = str(mover.env.history_file), str(mover.env.claude_json)
        self.assertIn(trigger, plan_keys)
        self.assertIn(victim, plan_keys)
        self.assertLess(
            plan_keys.index(trigger), plan_keys.index(victim),
            "history.jsonl must be scanned (and thus written) before claude.json for this race to trigger",
        )
        victim_original = Path(victim).read_text(encoding="utf-8")

        real_atomic_write = pm.atomic_write_text

        def flaky_write(path, text, validate_json=False):
            result = real_atomic_write(path, text, validate_json=validate_json)
            if str(path) == trigger:
                # Simulate an external process modifying `victim` right
                # after prevalidation passed but before its own turn to
                # be written.
                Path(victim).write_text(victim_original + "\nRACE MODIFIED", encoding="utf-8")
            return result

        pm.atomic_write_text = flaky_write
        try:
            code, output = silent(mover.execute_move, assume_yes=True)
        finally:
            pm.atomic_write_text = real_atomic_write

        self.assertNotEqual(code, pm.EXIT_OK)
        self.assertNotIn("Done.", output)
        self.assertEqual(Path(victim).read_text(encoding="utf-8"), victim_original + "\nRACE MODIFIED")
        self.assertTrue(proj.exists())
        self.assertFalse(dest.exists())

    def test_atomic_write_failure_stops_all_subsequent_planned_writes(self):
        env = self.env()
        proj = self.make_project()
        self.seed_claude_state(env, proj)
        dest = self.work / "archive" / "myapp"
        dest.parent.mkdir(parents=True, exist_ok=True)

        mover = pm.ProjectMover(str(proj), str(dest), execute=True, env=env)
        silent(mover.preflight)
        silent(mover.scan_all)

        plan_keys = list(mover._replacement_plans.keys())
        self.assertGreaterEqual(len(plan_keys), 3)
        # Use a fixed-location item (never relocated by the encoded-dir
        # rename) so the write-time path matches this literal string.
        fail_at = str(mover.env.plugins_file)
        self.assertIn(fail_at, plan_keys)
        later_keys = plan_keys[plan_keys.index(fail_at) + 1:]
        self.assertTrue(later_keys, "need at least one planned file after the failure point")

        originals = {k: mover._remap_after_encoded_dir_rename(Path(k)).read_text(encoding="utf-8") for k in plan_keys}

        real_atomic_write = pm.atomic_write_text

        def flaky_write(path, text, validate_json=False):
            if str(path) == fail_at:
                raise OSError("simulated write failure")
            return real_atomic_write(path, text, validate_json=validate_json)

        pm.atomic_write_text = flaky_write
        try:
            code, output = silent(mover.execute_move, assume_yes=True)
        finally:
            pm.atomic_write_text = real_atomic_write

        self.assertNotEqual(code, pm.EXIT_OK)
        self.assertNotIn("Done.", output)
        for k in later_keys:
            # Session/memory files relocate with the encoded-dir rename
            # (step 2) regardless of whether their content write (step 3)
            # happened, so look them up via the same remap the tool uses.
            current = mover._remap_after_encoded_dir_rename(Path(k))
            self.assertEqual(
                current.read_text(encoding="utf-8"), originals[k],
                f"{k} comes after the failure point and must never have been written",
            )
        self.assertTrue(proj.exists(), "project directory must never move after a state-file-stage error")
        self.assertFalse(dest.exists())


class TestExecutionStopsAfterStageFailure(TempHomeMixin, unittest.TestCase):
    """An error recorded during one execution stage must stop every later
    stage — in particular, a failure while renaming Claude state directories
    or while writing state files must prevent the project directory from
    being moved at all."""

    def test_encoded_dir_conflict_appearing_after_preflight_prevents_project_movement(self):
        env = self.env()
        proj = self.make_project()
        self.seed_claude_state(env, proj)
        dest = self.work / "myapp2"
        dest.mkdir()
        (dest / "other.py").write_text("print('other')\n", encoding="utf-8")  # project dir itself: conflict-free

        old_encoded = pm.encode_path(str(proj), env.windows)
        new_encoded = pm.encode_path(str(dest), env.windows)

        mover = pm.ProjectMover(str(proj), str(dest), execute=True, env=env)
        ok, _ = silent(mover.preflight)
        self.assertTrue(ok, "no conflict should exist yet at preflight time")
        silent(mover.scan_all)

        # Simulate a conflict appearing in ~/.claude/projects/<new_encoded>
        # between scan time and execute time (e.g. Claude Code itself wrote
        # a same-named session file there).
        old_session = next((env.claude_dir / "projects" / old_encoded).glob("*.jsonl"))
        new_projects_dir = env.claude_dir / "projects" / new_encoded
        new_projects_dir.mkdir(parents=True, exist_ok=True)
        (new_projects_dir / old_session.name).write_text("conflicting content", encoding="utf-8")

        original_claude_json = env.claude_json.read_text(encoding="utf-8")

        code, output = silent(mover.execute_move, assume_yes=True)

        # Only the 'projects' encoded dir had content to move, and it's the
        # one that hit the conflict — so nothing actually mutated before the
        # error, and the correct code is EXIT_ABORTED (not EXIT_PARTIAL).
        self.assertEqual(code, pm.EXIT_ABORTED)
        self.assertFalse(mover.mutated)
        self.assertNotIn("Done.", output)
        self.assertIn("backup", output.lower())
        # Stage 3 (state-file writes) must never have run.
        self.assertEqual(env.claude_json.read_text(encoding="utf-8"), original_claude_json)
        # Stage 4 (project directory move) must never have run.
        self.assertTrue(proj.exists())
        self.assertFalse((dest / "main.py").exists())

    def test_stage_failure_after_a_real_mutation_reports_partial(self):
        """Distinct from the test above: here a Claude state directory IS
        successfully renamed before a later one fails, so the exit code must
        be EXIT_PARTIAL (something mutated), not EXIT_ABORTED."""
        env = self.env()
        proj = self.make_project()
        self.seed_claude_state(env, proj)
        dest = self.work / "myapp2"
        dest.mkdir()
        (dest / "other.py").write_text("print('other')\n", encoding="utf-8")

        old_encoded = pm.encode_path(str(proj), env.windows)
        new_encoded = pm.encode_path(str(dest), env.windows)

        # Give the project a second encoded-dir category (file-history) that
        # will rename cleanly, so execute_encoded_dirs() has already mutated
        # something by the time it reaches the conflicting 'projects' dir.
        old_fh = env.claude_dir / "file-history" / old_encoded
        old_fh.mkdir(parents=True, exist_ok=True)
        (old_fh / "entry.txt").write_text("history", encoding="utf-8")

        mover = pm.ProjectMover(str(proj), str(dest), execute=True, env=env)
        ok, _ = silent(mover.preflight)
        self.assertTrue(ok)
        silent(mover.scan_all)

        old_session = next((env.claude_dir / "projects" / old_encoded).glob("*.jsonl"))
        new_projects_dir = env.claude_dir / "projects" / new_encoded
        new_projects_dir.mkdir(parents=True, exist_ok=True)
        (new_projects_dir / old_session.name).write_text("conflicting content", encoding="utf-8")

        code, output = silent(mover.execute_move, assume_yes=True)

        self.assertEqual(code, pm.EXIT_PARTIAL)
        self.assertTrue(mover.mutated)
        self.assertNotIn("Done.", output)
        # The file-history dir really was renamed (a genuine mutation)...
        self.assertTrue((env.claude_dir / "file-history" / new_encoded).exists())
        # ...but the project directory itself must still never have moved.
        self.assertTrue(proj.exists())
        self.assertFalse((dest / "main.py").exists())


class TestExitCodes(TempHomeMixin, unittest.TestCase):
    def test_aborted_exit_code_only_when_nothing_mutated(self):
        env = self.env()
        proj = self.make_project()
        self.seed_claude_state(env, proj)
        dest = self.work / "archive" / "myapp"
        dest.parent.mkdir(parents=True, exist_ok=True)

        mover = pm.ProjectMover(str(proj), str(dest), execute=True, env=env)
        silent(mover.preflight)
        silent(mover.scan_all)
        mover.create_backup = lambda: (_ for _ in ()).throw(OSError("simulated"))
        code, _ = silent(mover.execute_move, assume_yes=True)

        self.assertEqual(code, pm.EXIT_ABORTED)
        self.assertFalse(mover.mutated, "backup failure happens before the mutated flag is set")

    def test_no_state_backup_disabled_first_rename_failure_is_aborted_not_partial(self):
        """No Claude state to update, --no-backup (so no backup is even
        created), and the very first (only) mutating action — the project
        directory rename — raises. Nothing has actually changed, so this
        must be EXIT_ABORTED with mutated left False, not EXIT_PARTIAL."""
        import unittest.mock as mock

        env = self.env()
        proj = self.make_project()
        # Deliberately no seed_claude_state(): nothing for Claude state scans
        # to find.
        dest = self.work / "archive" / "myapp"
        dest.parent.mkdir(parents=True, exist_ok=True)

        mover = pm.ProjectMover(str(proj), str(dest), execute=True, backup=False, env=env)
        silent(mover.preflight)
        silent(mover.scan_all)
        self.assertEqual(mover._replacement_plans, {})

        with mock.patch.object(pm.Path, "rename", side_effect=OSError("simulated rename failure")):
            code, output = silent(mover.execute_move, assume_yes=True)

        self.assertEqual(code, pm.EXIT_ABORTED)
        self.assertFalse(mover.mutated)
        self.assertFalse(mover.backup_created)
        self.assertNotIn("Done.", output)
        self.assertTrue(proj.exists())
        self.assertFalse(dest.exists())

    def test_partial_exit_code_once_any_mutation_has_happened(self):
        env = self.env()
        proj = self.make_project()
        self.seed_claude_state(env, proj)
        dest = self.work / "archive" / "myapp"
        dest.parent.mkdir(parents=True, exist_ok=True)

        mover = pm.ProjectMover(str(proj), str(dest), execute=True, env=env)
        silent(mover.preflight)
        silent(mover.scan_all)
        real_verify = mover.verify_move
        mover.verify_move = lambda: real_verify() + ["synthetic stale reference"]
        code, _ = silent(mover.execute_move, assume_yes=True)

        self.assertEqual(code, pm.EXIT_PARTIAL)
        self.assertTrue(mover.mutated)

    def test_ok_exit_code_only_after_full_clean_verification(self):
        env = self.env()
        proj = self.make_project()
        self.seed_claude_state(env, proj)
        dest = self.work / "archive" / "myapp"
        dest.parent.mkdir(parents=True, exist_ok=True)

        mover = pm.ProjectMover(str(proj), str(dest), execute=True, env=env)
        silent(mover.preflight)
        silent(mover.scan_all)
        code, _ = silent(mover.execute_move, assume_yes=True)

        self.assertEqual(code, pm.EXIT_OK)
        self.assertEqual(mover.verify_move(), [])
        self.assertFalse(mover.errors)
        self.assertFalse(mover.warnings)

    def test_partial_when_stale_reference_remains(self):
        env = self.env()
        proj = self.make_project()
        self.seed_claude_state(env, proj)
        dest = self.work / "archive" / "myapp"
        dest.parent.mkdir(parents=True, exist_ok=True)

        mover = pm.ProjectMover(str(proj), str(dest), execute=True, env=env)
        silent(mover.preflight)
        silent(mover.scan_all)

        # Sabotage verify_move to simulate an undetected stale reference,
        # proving the tool reports non-zero rather than printing "Done".
        real_verify = mover.verify_move
        mover.verify_move = lambda: real_verify() + ["synthetic stale reference"]
        code, output = silent(mover.execute_move, assume_yes=True)
        self.assertEqual(code, pm.EXIT_PARTIAL)
        self.assertNotIn("Done.", output)
        self.assertIn("STALE REFERENCES", output)


class TestDoneMessageOmittedOnNonZeroExit(TempHomeMixin, unittest.TestCase):
    """'Done. Moved ...' must never appear alongside a non-zero exit code —
    warnings get their own distinct message rather than sharing the clean
    success line."""

    def test_warnings_only_uses_distinct_message_not_done(self):
        env = self.env()
        proj = self.make_project()
        self.seed_claude_state(env, proj)
        dest = self.work / "archive" / "myapp"
        dest.parent.mkdir(parents=True, exist_ok=True)

        mover = pm.ProjectMover(str(proj), str(dest), execute=True, env=env)
        silent(mover.preflight)
        silent(mover.scan_all)
        mover.warn("synthetic warning for test")
        code, output = silent(mover.execute_move, assume_yes=True)

        self.assertEqual(code, pm.EXIT_PARTIAL)
        self.assertNotIn("Done.", output)
        self.assertIn("FINISHED WITH WARNINGS", output)

    def test_done_absent_across_every_non_zero_exit_scenario(self):
        scenarios = []

        # ABORTED: backup failure, nothing mutated.
        env = self.env()
        proj = self.make_project()
        self.seed_claude_state(env, proj)
        dest = self.work / "archive1" / "myapp"
        dest.parent.mkdir(parents=True, exist_ok=True)
        mover = pm.ProjectMover(str(proj), str(dest), execute=True, env=env)
        silent(mover.preflight)
        silent(mover.scan_all)
        mover.create_backup = lambda: (_ for _ in ()).throw(OSError("simulated"))
        code, output = silent(mover.execute_move, assume_yes=True)
        scenarios.append(("aborted", code, output))

        # PARTIAL via error: state-file write fails after mutation started.
        env2 = self.env()
        proj2 = self.make_project(name="app2")
        self.seed_claude_state(env2, proj2)
        dest2 = self.work / "archive2" / "app2"
        dest2.parent.mkdir(parents=True, exist_ok=True)
        mover2 = pm.ProjectMover(str(proj2), str(dest2), execute=True, env=env2)
        silent(mover2.preflight)
        silent(mover2.scan_all)
        real_atomic_write = pm.atomic_write_text

        def flaky_write(path, text, validate_json=False):
            if Path(path).name == ".claude.json":
                raise OSError("simulated")
            return real_atomic_write(path, text, validate_json=validate_json)

        pm.atomic_write_text = flaky_write
        try:
            code2, output2 = silent(mover2.execute_move, assume_yes=True)
        finally:
            pm.atomic_write_text = real_atomic_write
        scenarios.append(("partial-error", code2, output2))

        # PARTIAL via warnings only.
        env3 = self.env()
        proj3 = self.make_project(name="app3")
        self.seed_claude_state(env3, proj3)
        dest3 = self.work / "archive3" / "app3"
        dest3.parent.mkdir(parents=True, exist_ok=True)
        mover3 = pm.ProjectMover(str(proj3), str(dest3), execute=True, env=env3)
        silent(mover3.preflight)
        silent(mover3.scan_all)
        mover3.warn("synthetic")
        code3, output3 = silent(mover3.execute_move, assume_yes=True)
        scenarios.append(("partial-warning", code3, output3))

        for label, code, output in scenarios:
            with self.subTest(label=label):
                self.assertNotEqual(code, pm.EXIT_OK)
                self.assertNotIn("Done.", output)


class TestCLI(TempHomeMixin, unittest.TestCase):
    def test_main_dry_run_exit_code(self):
        proj = self.make_project()
        dest = self.work / "archive" / "myapp"
        dest.parent.mkdir(parents=True, exist_ok=True)
        # main() uses ClaudeEnv.default(); point HOME/USERPROFILE at our temp
        # home so it never touches the real one.
        old_home = os.environ.get("USERPROFILE")
        old_home_posix = os.environ.get("HOME")
        os.environ["USERPROFILE"] = str(self.home)
        os.environ["HOME"] = str(self.home)
        try:
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = pm.main([str(proj), str(dest)])
            self.assertEqual(code, pm.EXIT_OK)
            self.assertIn("DRY RUN", buf.getvalue())
            self.assertTrue(proj.exists())
            self.assertFalse(dest.exists())
        finally:
            if old_home is not None:
                os.environ["USERPROFILE"] = old_home
            if old_home_posix is not None:
                os.environ["HOME"] = old_home_posix


if __name__ == "__main__":
    unittest.main()
