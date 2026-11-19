#![no_std]
#![forbid(unsafe_code)]
//! Pure launch planning. Filesystem access, networking and processes belong to the host.
extern crate alloc;
pub mod hash;
pub mod json;
pub mod model;
pub mod path;
pub mod plan;
pub use model::{Environment, Version, merge};
