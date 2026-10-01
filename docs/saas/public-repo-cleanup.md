# Public repo cleanup: removing creator-derived material from Git history

Status: **executed 2026-10-01.** The founder approved it, including decision C and the noreply author email.

What was done:
- `git filter-repo` ran on a clean clone with three changes: the path purge, the text replacements (required + optional identifiers), and a `--mailmap` to `64148111+areg06@users.noreply.github.com`.
- On GitHub, the old repository was deleted and `areg06/HayClip` was recreated as a public repo.
- Only the clean history was pushed. The new `main` starts at the rewritten root commit, and the last rewritten commit is `54437c6`.

Verification, from a fresh clone of the public repo:
- 0 removed paths, 0 quotes / test lines / ids, 0 old-email occurrences and 0 secret patterns across all 19 commits.
- The old commit `c480db0` returns 404 on the API, the web commit page and raw file URLs.
- The working copy now uses the clean `.git`, and the full suite (191 tests, including the pilot-03 regression) passes.
- The pre-rewrite `.git`, the pre-rewrite bundle and the replacement lists (which contained creator text) were deleted.

Kept privately (`~/.hayclips/backups/`, owner-only permissions):
- the paid-transcript recovery backup;
- a bundle of the clean history.

Commit SHAs cited elsewhere in `docs/saas/` refer to the pre-rewrite history.

The original plan follows, kept for the record.
The procedure was dry-run on a throwaway clone in the session scratchpad (§5).

## 1. Situation

| Item | Value |
|---|---|
| Remote | `https://github.com/areg06/HayClip.git`, **public**, default branch `main` |
| Remote refs | `refs/heads/main` → `c480db0` (pushed 2026-10-01 11:57 UTC). No tags, releases, PRs, forks, issues or wiki repository. The only collaborator is `areg06`. 0 watchers |
| Local refs | `main` = `c480db0` + 16 unpushed Phase 1a commits (`4f84a8d` … HEAD). No other branches, tags or stashes |
| Repo size | `.git` ≈ 5.6 MB. After the purge the pack is ≈ 0.43 MB |

## 2. Files proposed for removal from all history (path-based)

The exact list (19 paths) is in `docs/saas/cleanup/purge-paths.txt`. It holds file names only and is safe to commit.

| Path | In pushed `c480db0` | In current HEAD | Why |
|---|---|---|---|
| `research/caption-samples/_current_t5.png` | yes | yes | still from pilot-02 creator video |
| `research/caption-samples/A_active_word_t1.2.png`, `A_active_word_t12.0.png`, `A_active_word_longword_t2.0.png` | yes | yes | creator video frames with creator speech as captions |
| `research/caption-samples/A_active_word_on_speaker_crop_t12.0.png` | yes | yes | creator face crop |
| `research/caption-samples/B_one_word_pop_t1.2.png`, `B_one_word_pop_t12.0.png`, `B_one_word_pop_longword_t2.0.png` | yes | yes | creator frames and speech |
| `research/caption-samples/C_karaoke_box_t1.2.png`, `C_karaoke_box_t12.0.png` | yes | yes | creator frames and speech |
| `research/caption-samples/safezones_current.png`, `safezones_presetA.png` | yes | yes | creator frames |
| `research/caption-samples/caption_height_830_880_930_980.png` | yes | yes | pilot-03 creator face (4 frames) |
| `research/caption-samples/A_active_word.ass`, `B_one_word_pop.ass`, `C_karaoke_box.ass` | yes | yes | pilot-02 creator speech, word by word |
| `research/visual-samples/compare_t23_current_crop_punchin_reaction.jpg` | yes | yes | pilot-02 creator frames |
| `research/visual-samples/crop_x_expr.txt` | yes | yes | face positions measured on a creator video (numbers only, low sensitivity) |
| `research/.DS_Store` | yes | yes | macOS metadata. It can leak local file names |

**Kept:** `research/caption-samples/emoji_and_uppercase_test.{png,ass}`, which I checked: plain grey background and synthetic text only. Also kept: `research/caption-samples/make_samples.py`, `research/visual-samples/facetrack.py` and `render_samples.py`. These are scripts with no creator content; they read local pilot files that are not in git. The `.mp4` samples were never tracked, because `*.mp4` is gitignored.

## 3. Text removed from all history (content-based, `--replace-text`)

The expressions file holds the creator text itself, so it is stored **outside the repo**:
`~/.hayclips/cleanup/replace-required.txt` (chmod 600). **Never commit it.**

| Where | In `c480db0` | In HEAD | What |
|---|---|---|---|
| `research/cutting-retention.md`, lines 184–186, 233, 264, 315 | yes | yes | 8 verbatim quotes of the pilot-02 creator's speech, each replaced with «[creator quote removed]» |
| `tests/test_selection_captions.py`, `tests/test_captions_snap.py` | no | **no** | Present only in intermediate, unpushed Phase 1a commits. Commit `d369dc0` replaced them with synthetic Armenian in HEAD. The expressions map old to new, so every historical version of the tests stays consistent and passing |

**Left in place** (single common words, not identifying):
- `ուսումնասիրություն`
- `Մերսի`
- `ծյոծ էր`
- YouTube tags such as `[ծիծաղ]`

## 4. Optional: pilot identifiers (founder decision C)

These strings name the pilot shows and their public YouTube videos. They are not secrets and they do not contain creator
media, but they reveal who was approached for the pilot.

| Where | In `c480db0` | In HEAD |
|---|---|---|
| `README.md`, Status table: show names and one video id for pilots 01–03 | yes | yes |
| `tests/test_sources_youtube.py`, `tests/test_fetch_project.py`: pilot-03 video id used as a test id | no | no; HEAD uses `abcDEF12345` |

If C is approved:
- add `--replace-text ~/.hayclips/cleanup/replace-optional-identifiers.txt` (5 expressions) to step 3 of the procedure;
- reword the README Status table in a normal commit as well.

If C is declined:
- the identifiers stay in README history;
- the unpushed test commits can be pushed as they are, because the id is already public through README.

## 5. Procedure

Run only after the founder approves the rewrite.

```bash
# 0. Tools and freeze
brew install git-filter-repo        # or the pinned copy: .venv/bin/git-filter-repo (2.47.0)
# Make no commits and no pushes in other clones until step 9.

# 1. Backups (outside the repo)
cd ~/Desktop/hayclips
git bundle create ~/.hayclips/backups/hayclips-pre-rewrite-$(date +%Y%m%d).bundle --all
tar czf ~/.hayclips/backups/research-samples-$(date +%Y%m%d).tgz research/caption-samples research/visual-samples research/.DS_Store

# 2. Rewrite in a fresh clone; never in the working copy
git clone --no-local ~/Desktop/hayclips /tmp/hayclips-rewrite
cd /tmp/hayclips-rewrite
git filter-repo --force \
  --invert-paths --paths-from-file ~/Desktop/hayclips/docs/saas/cleanup/purge-paths.txt \
  --replace-text ~/.hayclips/cleanup/replace-required.txt
#   (+ --replace-text ~/.hayclips/cleanup/replace-optional-identifiers.txt   if decision C is approved;
#    filter-repo takes only one --replace-text file, so concatenate the two files first)

# 3. Verify (all must print 0 / pass)
git log --all --name-only --format= | grep -cE 'caption-samples/.*\.(png|ass)$|compare_t23|crop_x_expr|\.DS_Store'
while IFS= read -r l; do git grep -lF -- "${l%%==>*}" $(git rev-list --all); done < ~/.hayclips/cleanup/replace-required.txt | wc -l
git log --all -p | grep -cE 'hk_live_[A-Za-z0-9]{12,}|whsec_[A-Za-z0-9]{12,}'
git rev-list --count HEAD          # expect 17 (same number of commits as before)
PYTHONPATH=$PWD ~/Desktop/hayclips/.venv/bin/python -m pytest -q -p no:cacheprovider   # expect all pass (pilot/local tests skip)

# 4. Point the rewritten clone at GitHub
git remote add origin https://github.com/areg06/HayClip.git

# 5a. Force-push the rewritten history, including Phase 1a (recommended, see section 8):
git push --force-with-lease=main:c480db06e4d18c8d4bc5c7962fa45c250c9d2bf8 origin main
# 5b. Or push only the rewritten equivalent of c480db0, and Phase 1a later with a normal push:
#     NEW_ROOT=$(git rev-list --max-parents=0 HEAD)
#     git push --force-with-lease=main:c480db06e4d18c8d4bc5c7962fa45c250c9d2bf8 origin "$NEW_ROOT":main

# 6. GitHub-side caches: old commits stay reachable by SHA on github.com until GitHub garbage-collects them
#    Option 1 (recommended here: no forks, stars, issues, PRs or wiki): delete and recreate the repository,
#      then push the rewritten history to the new, empty repo.
#    Option 2: keep the repo and open a GitHub Support request
#      ("remove cached views / dangling commits", listing old SHA c480db0).

# 7. Swap the working copy onto the rewritten history without touching working files
cd ~/Desktop/hayclips
mv .git ~/.hayclips/backups/dotgit-pre-rewrite
cp -R /tmp/hayclips-rewrite/.git .git
git status        # purged files now show as ignored, not tracked. pilot-*/ and .venv are untouched
# Do NOT use `git reset --hard` from the old checkout: it would delete the research files from disk.

# 8. Post-rewrite follow-ups (normal commits)
#   - research/*.md: replace references to the purged stills with "regenerate locally" notes or synthetic stills (section 6)
#   - docs/saas/phase-1a-report.md: update the commit SHAs it cites (all SHAs change)

# 9. Clean up old copies once verified (they still contain the purged objects)
rm -rf /tmp/hayclips-rewrite ~/.hayclips/backups/dotgit-pre-rewrite
# Keep the bundle and tarball offline, or delete them if the founder prefers no copy at all.
```

**Dry-run result (2026-10-01, throwaway clone):**
- 17 commits in, 17 out.
- 0 purged paths remain in any commit.
- 0 of the 14 required strings remain in any commit.
- The rewritten HEAD differs from the current HEAD in exactly 20 files: the 19 purged files and `cutting-retention.md` (6 lines).
- Test suite on the rewritten HEAD: **187 passed, 4 skipped.** The skips are the pilot-03 regression and the local-fixture tests, which need gitignored data.
- Pack size: about 0.43 MB once the old objects are gone.

## 6. Replacement synthetic fixtures

- **Tests need none.** No test reads a purged file, as the dry-run test pass shows.
- **Research docs** (`caption-style.md` §7, `visual-audio.md` samples table) refer to the purged stills. Recommended follow-up:
  - add a `--synthetic` mode to `research/caption-samples/make_samples.py` that draws on an ffmpeg `testsrc2` or gradient background, with synthetic Armenian text (for example the lines now used in `tests/test_selection_captions.py`);
  - commit only those synthetic stills. Real-creator samples stay local and are gitignored (rules already in `.gitignore`).
- `tests/fixtures/local/` (pilot-03 face clips) is gitignored and was never committed. This was verified with `git ls-files`.

## 7. Secrets check (all history, all refs)

Every blob in every commit was scanned for: Harmar keys (`hk_live_…`), webhook secrets (`whsec_…`), bearer tokens,
`HARMAR_API_KEY=` assignments, OpenAI-style `sk-…`, AWS `AKIA…`, GitHub `ghp_…`, and PEM private keys.

**No secrets were ever committed.**

| Match | What it is |
|---|---|
| `hk_live_` | the documentation of the key prefix, and the test placeholder `"hk_live_whatever"` |
| `whsec_` | prose about the webhook secret only |
| `HARMAR_API_KEY=` | only `...` placeholders or `"$(cat ~/.config/harmar/key)"` |

The real key lives in `~/.config/harmar/key`, outside the repo.

Commit metadata shows the author's work email on every commit. It is already public through `c480db0`. Changing it
would need `--mailmap` in the same rewrite (founder decision).

## 8. Push Phase 1a before or after the rewrite?

**After. Do the rewrite first, then push Phase 1a, either in the same force-push (5a) or as a normal push (5b).**

1. Pushing Phase 1a first publishes more creator-derived material:
   - creator caption lines and the pilot video id inside intermediate test commits;
   - the research stills, still present in the HEAD tree.

   Every one of those commits would then need rewriting too.
2. The rewrite changes every commit SHA, because the root commit changes. Phase 1a commits pushed first would be
   orphaned anyway and need a second force-push. One rewrite plus one push is less churn and less exposure.
3. With no forks, PRs or other collaborators, the force-push has no downstream impact today. That will not stay true once others clone.

## 9. Collaborator impact

- **Today:** only `areg06`, with 0 forks, PRs, tags or releases.
- **Anyone who cloned or downloaded the repo since 11:57 UTC** still has the old history. A rewrite cannot reach those copies.
- **After the rewrite every SHA changes.** Clones must be replaced (`git clone` again), not pulled; a pull would merge the old history back in.
- **SHAs cited in docs become stale:** `phase-1a-report.md` and this file name old SHAs and must be updated after the rewrite.
- **Local data is not affected:** project and pilot folders, the Harmar caches and `~/.hayclips` are outside git.

## 10. Decisions for the founder

1. Approve the rewrite, and choose GitHub cache handling: recreate the repo (recommended) or a Support request.
2. Decision C: also replace the pilot show names and video ids?
3. Optionally rewrite the author email with `--mailmap`.
4. Keep or delete the offline backups (bundle and tarball), which contain the purged material.
