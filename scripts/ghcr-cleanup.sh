#!/usr/bin/env bash
# Removes old versions of the jumpseat image from ghcr.io, without breaking the ones that are kept.
#   scripts/ghcr-cleanup.sh            list what would be removed (dry run)
#   scripts/ghcr-cleanup.sh --delete   remove it
# Env: GH_TOKEN (packages read; write for --delete), OWNER (package owner), KEEP (fixed tags per image, default 3),
#      VERSIONS_FILE (optional: read the version list from this JSON file instead of the API, for testing).
#
# Each publish tags the joined image with a fixed tag, <latest|dev>-YYYYMMDD-<sha>. Kept are: every other tag
# (latest, dev, the -amd64/-arm64 tags, the build cache), the KEEP newest fixed tags of each image, and every version
# those tags point to. A multi-arch image is an index whose platform images and build attestations are untagged
# versions of their own, so "delete untagged versions" would break the current images; this follows the references.
set -euo pipefail

owner="${OWNER:?set OWNER to the package owner}"
keep="${KEEP:-3}"
package=jumpseat
image="ghcr.io/${owner}/${package}"
api="/users/${owner}/packages/container/${package}/versions"
release_re='^(latest|dev)-[0-9]{8}-[0-9a-f]{7,}$'

delete=false
if [[ "${1:-}" == "--delete" ]]; then
  delete=true
fi

# All versions as [{id, name (the digest), created_at, tags}]
if [[ -n "${VERSIONS_FILE:-}" ]]; then
  versions="$(jq '[.[] | {id, name, created_at, tags: (.metadata.container.tags // [])}]' "${VERSIONS_FILE}")"
else
  versions="$(gh api --paginate "${api}?per_page=100" \
    --jq '.[] | {id, name, created_at, tags: (.metadata.container.tags // [])}' | jq -s '.')"
fi
echo "Versions on ghcr.io: $(jq length <<<"${versions}")"

for tag in latest dev; do
  if ! jq -e --arg t "${tag}" 'any(.[]; any(.tags[]; . == $t))' <<<"${versions}" >/dev/null; then
    echo "The tag '${tag}' is missing from the version list; stopping without removing anything." >&2
    exit 1
  fi
done

# Tags to keep: everything that is not a fixed tag, plus the KEEP newest fixed tags of each image
mapfile -t keep_tags < <(
  {
    jq -r --arg re "${release_re}" '.[].tags[] | select(test($re) | not)' <<<"${versions}"
    for base in latest dev; do
      jq -r --arg re "^${base}-[0-9]{8}-[0-9a-f]{7,}$" --argjson keep "${keep}" '
        map(select(any(.tags[]; test($re)))) | sort_by(.created_at) | reverse | .[:$keep]
        | .[].tags[] | select(test($re))' <<<"${versions}"
    done
  } | sort -u
)
echo "Tags kept: ${keep_tags[*]}"

# Digests to keep: the kept tags' versions and everything they reference (indexes point to platform images and
# attestations, which are untagged)
declare -A keep_digests=()
queue=()
for tag in "${keep_tags[@]}"; do
  digest="$(jq -r --arg t "${tag}" 'first(.[] | select(any(.tags[]; . == $t)) | .name)' <<<"${versions}")"
  queue+=("${digest}")
done
while (( ${#queue[@]} > 0 )); do
  digest="${queue[0]}"
  queue=("${queue[@]:1}")
  [[ -n "${keep_digests[${digest}]:-}" ]] && continue
  keep_digests["${digest}"]=1
  # A failed lookup stops the script (set -e), so nothing is removed on an incomplete picture
  raw="$(docker buildx imagetools inspect --raw "${image}@${digest}")"
  while read -r child; do
    [[ -n "${child}" ]] && queue+=("${child}")
  done < <(jq -r '.manifests[]?.digest' <<<"${raw}")
done
echo "Versions kept: ${#keep_digests[@]}"

# Everything else goes
removed=0
while IFS=$'\t' read -r id name created tags; do
  [[ -n "${keep_digests[${name}]:-}" ]] && continue
  removed=$((removed + 1))
  if [[ "${delete}" == true ]]; then
    echo "remove ${id} ${created} ${name} ${tags}"
    gh api --method DELETE "${api}/${id}" >/dev/null
  else
    echo "would remove ${id} ${created} ${name} ${tags}"
  fi
done < <(jq -r '.[] | [.id, .name, .created_at, (.tags | join(","))] | @tsv' <<<"${versions}")

if [[ "${delete}" == true ]]; then
  echo "Removed ${removed} versions."
else
  echo "Dry run: ${removed} versions would be removed (run with --delete to remove them)."
fi
