# Releasing the Python package

1. **Prepare the release on a branch.** Set the new version in `src/lmc5/__init__.py`. Setuptools reads that literal for package metadata, and the CLI imports it. Move the shipped entries from `Unreleased` into a dated section of the changelog. Include the user-visible changes and migration notes.
2. **Merge and verify the exact commit.** Merge the PR into `main`. Record the full merge SHA and the successful GitHub Actions **push** run for that SHA. Check the Python matrix, AML adapter, PostgreSQL and wheel jobs. A PR run can have a different commit.
3. **Create an annotated tag.** From a clean checkout, use the version and the successful run ID for this release. Review the remote tag lookup before creating anything. An existing public release tag stays at its original commit.

   ```sh
   git fetch --tags origin main
   sha=$(git rev-parse origin/main)
   git switch --detach "$sha"
   version=$(PYTHONPATH=src python -c 'import lmc5; print(lmc5.__version__)')
   tag="v$version"
   gh run list --workflow CI --branch main --event push --commit "$sha"
   git ls-remote --tags origin "refs/tags/$tag"
   ```

   After confirming a successful run for `$sha` and an unused tag name:

   ```sh
   git tag -a "$tag" "$sha" -m "LMC-5 $version"
   git push origin "refs/tags/$tag"
   git ls-remote origin "refs/tags/$tag^{}"
   ```

   Verify that the last command reports the full tested SHA. If a published release needs a correction, issue a new patch version.

4. **Build from the public tag.** In a fresh checkout of that tag with an empty `dist/`, install `build` and `twine` into a build environment. Run `python -m build` and `python -m twine check --strict dist/*`. Install the resulting wheel into a separate clean environment outside the checkout. Verify `lmc5 --version`, `python -Im lmc5 --version` and a CLI write followed by recall. The [wheel job](../.github/workflows/ci.yml) contains the smoke test.
5. **Attach the verified artifacts.** In the build checkout, create a checksum list with filenames relative to `dist/`. Verify the list before uploading the wheel, sdist and checksums to the GitHub Release for the existing tag.

   ```sh
   (cd dist && shasum -a 256 *.whl *.tar.gz > SHA256SUMS && shasum -a 256 -c SHA256SUMS)
   gh release create "$tag" --verify-tag --title "LMC-5 $version" \
     --notes-file "$release_notes" dist/*.whl dist/*.tar.gz dist/SHA256SUMS
   ```

   Set `release_notes` to a file containing this version’s changelog entry and the validation link. Download the assets into a fresh directory and verify `shasum -a 256 -c SHA256SUMS` there. Record the release URL. A PyPI upload is a separate publication decision.
