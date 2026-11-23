//! Streaming digest primitives for Mojang asset integrity and legacy offline UUIDs.
//! SHA-1 and MD5 are compatibility hashes, not password storage or signatures.
use alloc::{format, string::String};
use sha2::Digest;

#[derive(Clone)]
struct Blocks {
    block: [u8; 64],
    used: usize,
    bytes: u64,
}
impl Blocks {
    const fn new() -> Self {
        Self {
            block: [0; 64],
            used: 0,
            bytes: 0,
        }
    }
    fn update(&mut self, mut input: &[u8], mut compress: impl FnMut(&[u8; 64])) {
        self.bytes = self.bytes.wrapping_add(input.len() as u64);
        if self.used != 0 {
            let n = (64 - self.used).min(input.len());
            self.block[self.used..self.used + n].copy_from_slice(&input[..n]);
            self.used += n;
            input = &input[n..];
            if self.used == 64 {
                compress(&self.block);
                self.used = 0;
            }
        }
        while input.len() >= 64 {
            let (block, rest) = input.split_at(64);
            compress(block.try_into().expect("exact block"));
            input = rest;
        }
        if !input.is_empty() {
            self.block[..input.len()].copy_from_slice(input);
            self.used = input.len();
        }
    }
    fn finish(mut self, little_endian: bool, mut compress: impl FnMut(&[u8; 64])) {
        let bits = self.bytes.wrapping_mul(8);
        self.update(&[0x80], &mut compress);
        let padding = if self.used <= 56 {
            56 - self.used
        } else {
            120 - self.used
        };
        self.update(&[0; 64][..padding], &mut compress);
        self.update(
            &if little_endian {
                bits.to_le_bytes()
            } else {
                bits.to_be_bytes()
            },
            compress,
        );
    }
}

#[derive(Clone, Default)]
pub struct Sha1 {
    // Lazy initialization preserves the public const constructor.
    inner: Option<sha1::Sha1>,
}
impl Sha1 {
    pub const fn new() -> Self {
        Self { inner: None }
    }
    pub fn update(&mut self, bytes: &[u8]) {
        self.inner.get_or_insert_with(sha1::Sha1::new).update(bytes);
    }
    pub fn finalize(self) -> [u8; 20] {
        self.inner.unwrap_or_default().finalize().into()
    }
}

#[derive(Clone, Default)]
pub struct Sha256 {
    // Lazy initialization preserves the public const constructor.
    inner: Option<sha2::Sha256>,
}
impl Sha256 {
    pub const fn new() -> Self {
        Self { inner: None }
    }
    pub fn update(&mut self, bytes: &[u8]) {
        self.inner
            .get_or_insert_with(sha2::Sha256::new)
            .update(bytes);
    }
    pub fn finalize(self) -> [u8; 32] {
        self.inner.unwrap_or_default().finalize().into()
    }
}

#[derive(Clone)]
pub struct Md5 {
    blocks: Blocks,
    state: [u32; 4],
}
impl Default for Md5 {
    fn default() -> Self {
        Self::new()
    }
}
impl Md5 {
    pub const fn new() -> Self {
        Self {
            blocks: Blocks::new(),
            state: [0x67452301, 0xefcdab89, 0x98badcfe, 0x10325476],
        }
    }
    pub fn update(&mut self, bytes: &[u8]) {
        let state = &mut self.state;
        self.blocks.update(bytes, |block| md5_block(state, block));
    }
    pub fn finalize(mut self) -> [u8; 16] {
        self.blocks
            .finish(true, |block| md5_block(&mut self.state, block));
        let mut out = [0; 16];
        for (word, chunk) in self.state.iter().zip(out.as_chunks_mut::<4>().0.iter_mut()) {
            chunk.copy_from_slice(&word.to_le_bytes());
        }
        out
    }
}
fn md5_block(s: &mut [u32; 4], input: &[u8; 64]) {
    const ROT: [u32; 64] = [
        7, 12, 17, 22, 7, 12, 17, 22, 7, 12, 17, 22, 7, 12, 17, 22, 5, 9, 14, 20, 5, 9, 14, 20, 5,
        9, 14, 20, 5, 9, 14, 20, 4, 11, 16, 23, 4, 11, 16, 23, 4, 11, 16, 23, 4, 11, 16, 23, 6, 10,
        15, 21, 6, 10, 15, 21, 6, 10, 15, 21, 6, 10, 15, 21,
    ];
    const K: [u32; 64] = [
        0xd76aa478, 0xe8c7b756, 0x242070db, 0xc1bdceee, 0xf57c0faf, 0x4787c62a, 0xa8304613,
        0xfd469501, 0x698098d8, 0x8b44f7af, 0xffff5bb1, 0x895cd7be, 0x6b901122, 0xfd987193,
        0xa679438e, 0x49b40821, 0xf61e2562, 0xc040b340, 0x265e5a51, 0xe9b6c7aa, 0xd62f105d,
        0x02441453, 0xd8a1e681, 0xe7d3fbc8, 0x21e1cde6, 0xc33707d6, 0xf4d50d87, 0x455a14ed,
        0xa9e3e905, 0xfcefa3f8, 0x676f02d9, 0x8d2a4c8a, 0xfffa3942, 0x8771f681, 0x6d9d6122,
        0xfde5380c, 0xa4beea44, 0x4bdecfa9, 0xf6bb4b60, 0xbebfbc70, 0x289b7ec6, 0xeaa127fa,
        0xd4ef3085, 0x04881d05, 0xd9d4d039, 0xe6db99e5, 0x1fa27cf8, 0xc4ac5665, 0xf4292244,
        0x432aff97, 0xab9423a7, 0xfc93a039, 0x655b59c3, 0x8f0ccc92, 0xffeff47d, 0x85845dd1,
        0x6fa87e4f, 0xfe2ce6e0, 0xa3014314, 0x4e0811a1, 0xf7537e82, 0xbd3af235, 0x2ad7d2bb,
        0xeb86d391,
    ];
    let mut m = [0u32; 16];
    for (i, b) in input.as_chunks::<4>().0.iter().enumerate() {
        m[i] = u32::from_le_bytes(*b);
    }
    let [mut a, mut b, mut c, mut d] = *s;
    for i in 0..64 {
        let (f, g) = match i {
            0..16 => ((b & c) | (!b & d), i),
            16..32 => ((d & b) | (!d & c), (5 * i + 1) % 16),
            32..48 => (b ^ c ^ d, (3 * i + 5) % 16),
            _ => (c ^ (b | !d), (7 * i) % 16),
        };
        let next = b.wrapping_add(
            a.wrapping_add(f)
                .wrapping_add(K[i])
                .wrapping_add(m[g])
                .rotate_left(ROT[i]),
        );
        a = d;
        d = c;
        c = b;
        b = next;
    }
    for (v, x) in s.iter_mut().zip([a, b, c, d]) {
        *v = v.wrapping_add(x);
    }
}

pub fn hex(bytes: &[u8]) -> String {
    let mut out = alloc::vec![0; zhex::encoded_len(bytes.len()).expect("hex length overflow")];
    zhex::encode_lower(bytes, &mut out).expect("exact hex output capacity");
    String::from_utf8(out).expect("hex output is ASCII")
}
pub fn sha1_hex(bytes: &[u8]) -> String {
    let mut h = Sha1::new();
    h.update(bytes);
    hex(&h.finalize())
}
pub fn sha256_hex(bytes: &[u8]) -> String {
    let mut h = Sha256::new();
    h.update(bytes);
    hex(&h.finalize())
}
pub fn md5_hex(bytes: &[u8]) -> String {
    let mut h = Md5::new();
    h.update(bytes);
    hex(&h.finalize())
}

/// Java UUID.nameUUIDFromBytes("OfflinePlayer:" + name), without hyphens.
pub fn offline_uuid(name: &str) -> String {
    let mut md5 = Md5::new();
    md5.update(b"OfflinePlayer:");
    md5.update(name.as_bytes());
    let mut bytes = md5.finalize();
    bytes[6] = (bytes[6] & 0x0f) | 0x30;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    hex(&bytes)
}
pub fn hyphenated_uuid(uuid: &str) -> Result<String, String> {
    if uuid.len() != 32 || !uuid.bytes().all(|b| b.is_ascii_hexdigit()) {
        return Err("Invalid UUID".into());
    }
    Ok(format!(
        "{}-{}-{}-{}-{}",
        &uuid[..8],
        &uuid[8..12],
        &uuid[12..16],
        &uuid[16..20],
        &uuid[20..]
    ))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn lowercase_hex_matches_wire_format() {
        let bytes: [u8; 4097] = core::array::from_fn(|index| index as u8);
        for length in [
            0, 1, 15, 16, 17, 20, 31, 32, 33, 63, 64, 65, 255, 256, 257, 4096, 4097,
        ] {
            let input = &bytes[..length];
            let expected: String = input.iter().map(|byte| format!("{byte:02x}")).collect();
            assert_eq!(hex(input), expected, "input length {length}");
        }
        assert_eq!(hex(b"\x00\x0f\x10\x7f\x80\xff"), "000f107f80ff");
    }

    #[test]
    fn digest_const_constructors_and_clones_keep_independent_states() {
        const EMPTY_SHA1: Sha1 = Sha1::new();
        const EMPTY_SHA256: Sha256 = Sha256::new();
        assert_eq!(
            hex(&EMPTY_SHA1.finalize()),
            "da39a3ee5e6b4b0d3255bfef95601890afd80709"
        );
        assert_eq!(
            hex(&EMPTY_SHA256.finalize()),
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        );
        assert_eq!(Sha1::default().finalize(), EMPTY_SHA1.finalize());
        assert_eq!(Sha256::default().finalize(), EMPTY_SHA256.finalize());

        let mut sha1 = EMPTY_SHA1;
        sha1.update(b"");
        sha1.update(b"ab");
        let mut sha1_branch = sha1.clone();
        sha1.update(b"c");
        sha1_branch.update(b"d");
        assert_eq!(
            hex(&sha1.finalize()),
            "a9993e364706816aba3e25717850c26c9cd0d89d"
        );
        assert_eq!(
            hex(&sha1_branch.finalize()),
            "cb4cc28df0fdbe0ecf9d9662e294b118092a5735"
        );

        let mut sha256 = EMPTY_SHA256;
        sha256.update(b"");
        sha256.update(b"ab");
        let mut sha256_branch = sha256.clone();
        sha256.update(b"c");
        sha256_branch.update(b"d");
        assert_eq!(
            hex(&sha256.finalize()),
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        );
        assert_eq!(
            hex(&sha256_branch.finalize()),
            "a52d159f262b2c6ddb724a61840befc36eb30c88877a4030b65cbe86298449c9"
        );
    }

    #[test]
    fn standard_digest_vectors() {
        assert_eq!(sha1_hex(b""), "da39a3ee5e6b4b0d3255bfef95601890afd80709");
        assert_eq!(sha1_hex(b"abc"), "a9993e364706816aba3e25717850c26c9cd0d89d");
        assert_eq!(
            sha256_hex(b""),
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        );
        assert_eq!(
            sha256_hex(b"abc"),
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        );
        assert_eq!(md5_hex(b""), "d41d8cd98f00b204e9800998ecf8427e");
        assert_eq!(md5_hex(b"abc"), "900150983cd24fb0d6963f7d28e17f72");
        assert_eq!(
            md5_hex(b"message digest"),
            "f96b697d7cb7938d525a2f31aaf161d0"
        );
    }
    #[test]
    fn million_a_streaming_vectors() {
        let mut a = Sha1::new();
        let mut b = Sha256::new();
        let mut c = Md5::new();
        for _ in 0..1000 {
            a.update(&[b'a'; 1000]);
            b.update(&[b'a'; 1000]);
            c.update(&[b'a'; 1000]);
        }
        assert_eq!(
            hex(&a.finalize()),
            "34aa973cd4c4daa4f61eeb2bdbad27316534016f"
        );
        assert_eq!(
            hex(&b.finalize()),
            "cdc76e5c9914fb9281a1c7e284d73e67f1809a48a497200e046d39ccc7112cd0"
        );
        assert_eq!(hex(&c.finalize()), "7707d6ae4e027c70eea2a935c2296f21");
    }
    #[test]
    fn every_chunk_boundary_matches_whole_digest() {
        let bytes: [u8; 257] = core::array::from_fn(|i| i as u8);
        for size in 1..=80 {
            for length in [
                0, 1, 55, 56, 57, 63, 64, 65, 119, 120, 121, 127, 128, 129, 256, 257,
            ] {
                let input = &bytes[..length];
                let mut a = Sha1::new();
                let mut b = Sha256::new();
                let mut c = Md5::new();
                for chunk in input.chunks(size) {
                    a.update(chunk);
                    b.update(chunk);
                    c.update(chunk);
                }
                assert_eq!(hex(&a.finalize()), sha1_hex(input));
                assert_eq!(hex(&b.finalize()), sha256_hex(input));
                assert_eq!(hex(&c.finalize()), md5_hex(input));
            }
        }
    }
    #[test]
    fn java_offline_uuid_vector() {
        assert_eq!(offline_uuid("Notch"), "b50ad385829d3141a2167e7d7539ba7f");
        assert_eq!(
            hyphenated_uuid(&offline_uuid("Notch")).unwrap(),
            "b50ad385-829d-3141-a216-7e7d7539ba7f"
        );
    }
}
