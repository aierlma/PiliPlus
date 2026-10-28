# GitHub iOS builds and publication checks

Build personal iOS packages with GitHub Actions. The local iOS build entrypoint
has been retired. Use **BTR and PiliPlus sync and iOS release** for normal
maintenance and publication, or **Build for iOS** for a test artifact. Install
downloaded unsigned packages through SideStore.

## Account and source identity

The personal repository is `aierlma/PiliPlus`. Human commits use `aierlma` and
the matching GitHub noreply address. Automated merges use the official
`github-actions[bot]` identity. This policy checks the publication commit;
inherited upstream authors are preserved.

Configure the repository Actions secret `RELEASE_PRIVATE_TERMS_JSON` as a
nonempty JSON array of private local account names or identifiers that must
not appear in published source. Do not commit the values or put them in public
workflow variables. The source check refuses missing or invalid configuration,
unexpected commit identities, unreadable files and matches. Its output contains
rule names and numeric locations, not the matched private values or paths.

## Package checks

Before packaging, `tool/prepare_public_ios.py` signs frameworks ad hoc to
normalize the link-edit layout of prebuilt libraries, then removes native
debug symbols with `xcrun strip -S`. It signs frameworks again after stripping
and verifies every final signature strictly. A failure in any stage stops
packaging. This does not remove
source URIs embedded in Dart AOT code, which is why public packages are built
in GitHub Actions rather than in a personal home directory.

After the final IPA is produced, `tool/release_privacy.py` scans archive
contents, metadata and supported nested compressed files. The check covers
personal home-directory paths, signing profiles and keys, device identifiers
and recognizable credentials. Generic GitHub runner paths are explicitly
allowed; the checked-in policy contains no third-party member exceptions.
Malformed files, unsupported archives and scan limits stop publication.

Every iOS artifact upload runs this check. The formal publisher independently
rescans its downloaded IPA, verifies the authenticated repository identity,
uploads the same checked staging copy and compares the remote asset digest
before making the release public. Changing the package preparation code or
privacy policy requires a new package, even when application code is unchanged.

The report records the artifact SHA-256, size, scanner and policy identities.
Pattern checks cannot prove the absence of every possible encoded secret;
their purpose is to reject known exposure classes and incomplete scans before
anything is uploaded. Do not bypass a failed check by changing an allowlist.
