# zmcl maintenance

## Product and claims

zmcl is a new Project Z Minecraft launcher. Full XMCL functional coverage is the target.
Use docs/FEATURES.md and its fixed upstream version to track actual capability.
An implemented path is not automatically verified. Record the tested OS, game,
loader, account mode, artifact hash and actual outcome. Never invent benchmarks,
compare a whole process tree with a single process, or include Minecraft/Java
memory in only one side of a launcher comparison.

## Working tree and validation

Development rolls on `main` as the sole development branch. Coordinate one writer
before changing the shared branch or index, and publish tested batches directly
to `main`. Do not create feature branches for new work. Temporary detached
snapshots may isolate preparation while another owner edits a file. Preserve
unrelated work, then integrate through the main writer. Retire an old branch
only after all its commits are integrated and no active work depends on it.
Run local Cargo tests, Clippy with warnings denied and the changed Python helper
tests. Use real install/launch or file roundtrips for changed user-visible paths.
Do not trigger routine remote CI or CodeQL as an acceptance gate. Documentation
publication and explicitly requested release workflows have their own purpose.
Commit and push meaningful verified batches, then verify the remote commit.

## Project Z propagation

When a foundational library changes, its owner must enumerate and update every
direct and transitive consumer, including libraries, applications, services and
installers. Use Project Z's `scripts/project_z.py impact --repositories NAME`,
inspect manifests, register missing relationships and coordinate with active
owners. Update compatibility code, exact dependency versions, source gitlinks and
Cargo.lock in dependency order. Test affected behavior and publish each source
commit before advancing aggregate pins. A notification alone does not complete
propagation: record the exact blocker, consumer, owner and reusable commit.

Cross-repository Project Z dependencies use canonical GitHub Git sources and
follow the latest default branches. Refresh Cargo.lock and internal source
gitlinks together, retaining version metadata for external Cargo publication.
Same-repository workspace paths remain local. Do not edit vendored repositories
to hide an upstream incompatibility.
The launch-planning crate remains no_std + alloc and has no OS side effects.

## Resource and data boundaries

Keep idle UI work event driven. Heavy helpers run only during requested operations
and exit afterwards. System libraries and programs remain real dependencies even
when the Rust dependency graph contains only Project Z crates.

Never log credentials or commit account data, game saves, private paths, raw
session logs or benchmark profiles. Use operating-system secret storage.
Downloads require expected digests and limits. Imports publish from checked
staging; reversible removal goes to trash. Do not mutate a running game instance.
