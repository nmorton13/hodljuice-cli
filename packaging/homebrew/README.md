# Releasing and the Homebrew tap

`hj.rb` here is the formula for the tap repo `github.com/nmorton13/homebrew-hodljuice`, which makes
`brew install nmorton13/hodljuice/hj` work (Homebrew taps the repo on first use).

It installs `hj` into its own Python 3.14 virtualenv, uses Homebrew's prebuilt `pydantic`, `cryptography`
and `rpds-py` instead of compiling them, vendors every other Python dependency as a `resource`, depends on
`mpv`, and puts the Claude Code mod and its marketplace file in `$(brew --prefix)/opt/hj/share/hodljuice`.

## Cut a release

1. Bump `version` in `pyproject.toml` and `VERSION` in `src/hodljuice_cli/entry.py`, and `version` in
   `claude-mod/.claude-plugin/plugin.json`.
2. Commit, tag and push:
   ```sh
   git tag v0.1.0 && git push origin main v0.1.0
   ```
3. Point the formula at the tag and fill in the checksum:
   ```sh
   url=https://github.com/nmorton13/hodljuice-cli/archive/refs/tags/v0.1.0.tar.gz
   curl -sL "$url" | shasum -a 256
   ```
   Put the URL in `url` and the hash in `sha256` (the formula ships with a placeholder of zeros).
4. If dependencies changed, regenerate the `resource` blocks (needs the formula in a tap; see below):
   ```sh
   brew update-python-resources nmorton13/hodljuice/hj
   ```
   Never edit those blocks by hand. The `pypi_packages exclude_packages:` line keeps the compiled packages
   on Homebrew's own formulae.

## Publish the tap (first time)

```sh
gh repo create nmorton13/homebrew-hodljuice --public
git clone https://github.com/nmorton13/homebrew-hodljuice && cd homebrew-hodljuice
mkdir Formula && cp ../hodljuice-cli/packaging/homebrew/hj.rb Formula/
git add Formula/hj.rb && git commit -m "hj 0.1.0" && git push
```

For later releases, copy the updated `hj.rb` into `Formula/` and push.

## Check it

```sh
brew install --build-from-source nmorton13/hodljuice/hj
brew test hj
brew audit --strict --online nmorton13/hodljuice/hj
```

### Before the first release: test against a local tarball

```sh
brew tap-new --no-git nmorton13/hjtest
git archive --prefix=hodljuice-cli-0.1.0/ -o /tmp/hodljuice-cli-0.1.0.tar.gz HEAD   # same layout as GitHub's
# copy hj.rb into $(brew --repository)/Library/Taps/nmorton13/homebrew-hjtest/Formula/, with
#   url "file:///tmp/hodljuice-cli-0.1.0.tar.gz"   and that file's sha256
brew install --build-from-source nmorton13/hjtest/hj && brew test nmorton13/hjtest/hj
brew uninstall hj && brew untap nmorton13/hjtest
```

The formula has been built, tested and audited this way on macOS (Apple Silicon). It hasn't been tested
with Homebrew on Linux.
