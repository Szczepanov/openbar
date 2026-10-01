//! Dependency-free SHA-256 (FIPS 180-4) used to bind decoded media to fixture identity.

use std::fs::File;
use std::io::{self, Read};
use std::path::Path;

const INITIAL_STATE: [u32; 8] = [
    0x6a09_e667,
    0xbb67_ae85,
    0x3c6e_f372,
    0xa54f_f53a,
    0x510e_527f,
    0x9b05_688c,
    0x1f83_d9ab,
    0x5be0_cd19,
];

const ROUND_CONSTANTS: [u32; 64] = [
    0x428a_2f98,
    0x7137_4491,
    0xb5c0_fbcf,
    0xe9b5_dba5,
    0x3956_c25b,
    0x59f1_11f1,
    0x923f_82a4,
    0xab1c_5ed5,
    0xd807_aa98,
    0x1283_5b01,
    0x2431_85be,
    0x550c_7dc3,
    0x72be_5d74,
    0x80de_b1fe,
    0x9bdc_06a7,
    0xc19b_f174,
    0xe49b_69c1,
    0xefbe_4786,
    0x0fc1_9dc6,
    0x240c_a1cc,
    0x2de9_2c6f,
    0x4a74_84aa,
    0x5cb0_a9dc,
    0x76f9_88da,
    0x983e_5152,
    0xa831_c66d,
    0xb003_27c8,
    0xbf59_7fc7,
    0xc6e0_0bf3,
    0xd5a7_9147,
    0x06ca_6351,
    0x1429_2967,
    0x27b7_0a85,
    0x2e1b_2138,
    0x4d2c_6dfc,
    0x5338_0d13,
    0x650a_7354,
    0x766a_0abb,
    0x81c2_c92e,
    0x9272_2c85,
    0xa2bf_e8a1,
    0xa81a_664b,
    0xc24b_8b70,
    0xc76c_51a3,
    0xd192_e819,
    0xd699_0624,
    0xf40e_3585,
    0x106a_a070,
    0x19a4_c116,
    0x1e37_6c08,
    0x2748_774c,
    0x34b0_bcb5,
    0x391c_0cb3,
    0x4ed8_aa4a,
    0x5b9c_ca4f,
    0x682e_6ff3,
    0x748f_82ee,
    0x78a5_636f,
    0x84c8_7814,
    0x8cc7_0208,
    0x90be_fffa,
    0xa450_6ceb,
    0xbef9_a3f7,
    0xc671_78f2,
];

const BLOCK_LEN: usize = 64;

pub struct Sha256 {
    state: [u32; 8],
    pending: [u8; BLOCK_LEN],
    pending_len: usize,
    total_len_bytes: u64,
}

impl Sha256 {
    pub fn new() -> Self {
        Self {
            state: INITIAL_STATE,
            pending: [0; BLOCK_LEN],
            pending_len: 0,
            total_len_bytes: 0,
        }
    }

    pub fn update(&mut self, mut data: &[u8]) {
        self.total_len_bytes = self.total_len_bytes.wrapping_add(data.len() as u64);
        if self.pending_len > 0 {
            let take = (BLOCK_LEN - self.pending_len).min(data.len());
            self.pending[self.pending_len..self.pending_len + take].copy_from_slice(&data[..take]);
            self.pending_len += take;
            data = &data[take..];
            if self.pending_len < BLOCK_LEN {
                return;
            }
            let block = self.pending;
            self.compress(&block);
            self.pending_len = 0;
        }
        let (blocks, rest) = data.as_chunks::<BLOCK_LEN>();
        for block in blocks {
            self.compress(block);
        }
        self.pending[..rest.len()].copy_from_slice(rest);
        self.pending_len = rest.len();
    }

    pub fn finalize_hex(mut self) -> String {
        let bit_len = self.total_len_bytes.wrapping_mul(8);
        let mut padding = vec![0x80u8];
        let zeros = (BLOCK_LEN + 56 - (self.pending_len + 1) % BLOCK_LEN) % BLOCK_LEN;
        padding.resize(1 + zeros, 0);
        padding.extend_from_slice(&bit_len.to_be_bytes());
        // Padding is not message content, so it must not advance the length counter.
        let total = self.total_len_bytes;
        self.update(&padding);
        self.total_len_bytes = total;
        debug_assert_eq!(self.pending_len, 0);
        self.state
            .iter()
            .map(|word| format!("{word:08x}"))
            .collect()
    }

    fn compress(&mut self, block: &[u8; BLOCK_LEN]) {
        let mut schedule = [0u32; 64];
        for (index, word) in block.as_chunks::<4>().0.iter().enumerate() {
            schedule[index] = u32::from_be_bytes(*word);
        }
        for index in 16..64 {
            let s0 = schedule[index - 15].rotate_right(7)
                ^ schedule[index - 15].rotate_right(18)
                ^ (schedule[index - 15] >> 3);
            let s1 = schedule[index - 2].rotate_right(17)
                ^ schedule[index - 2].rotate_right(19)
                ^ (schedule[index - 2] >> 10);
            schedule[index] = schedule[index - 16]
                .wrapping_add(s0)
                .wrapping_add(schedule[index - 7])
                .wrapping_add(s1);
        }

        let [mut a, mut b, mut c, mut d, mut e, mut f, mut g, mut h] = self.state;
        for index in 0..64 {
            let sum1 = e.rotate_right(6) ^ e.rotate_right(11) ^ e.rotate_right(25);
            let choice = (e & f) ^ (!e & g);
            let temp1 = h
                .wrapping_add(sum1)
                .wrapping_add(choice)
                .wrapping_add(ROUND_CONSTANTS[index])
                .wrapping_add(schedule[index]);
            let sum0 = a.rotate_right(2) ^ a.rotate_right(13) ^ a.rotate_right(22);
            let majority = (a & b) ^ (a & c) ^ (b & c);
            let temp2 = sum0.wrapping_add(majority);
            h = g;
            g = f;
            f = e;
            e = d.wrapping_add(temp1);
            d = c;
            c = b;
            b = a;
            a = temp1.wrapping_add(temp2);
        }
        for (slot, value) in self.state.iter_mut().zip([a, b, c, d, e, f, g, h]) {
            *slot = slot.wrapping_add(value);
        }
    }
}

impl Default for Sha256 {
    fn default() -> Self {
        Self::new()
    }
}

pub fn file_sha256_hex(path: &Path) -> io::Result<String> {
    let mut file = File::open(path)?;
    let mut hasher = Sha256::new();
    let mut buffer = vec![0u8; 1 << 20];
    loop {
        let read = match file.read(&mut buffer) {
            Ok(read) => read,
            Err(error) if error.kind() == io::ErrorKind::Interrupted => continue,
            Err(error) => return Err(error),
        };
        if read == 0 {
            return Ok(hasher.finalize_hex());
        }
        hasher.update(&buffer[..read]);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn digest(data: &[u8]) -> String {
        let mut hasher = Sha256::new();
        hasher.update(data);
        hasher.finalize_hex()
    }

    #[test]
    fn matches_fips_180_test_vectors() {
        assert_eq!(
            digest(b""),
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        );
        assert_eq!(
            digest(b"abc"),
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        );
        assert_eq!(
            digest(b"abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq"),
            "248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1"
        );
        // Lengths straddling the padding boundary (55 fits one block, 56 needs two).
        assert_eq!(
            digest(&[b'a'; 55]),
            "9f4390f8d30c2dd92ec9f095b65e2b9ae9b0a925a5258e241c9f1e910f734318"
        );
        assert_eq!(
            digest(&[b'a'; 56]),
            "b35439a4ac6f0948b6d6f9e3c6af0f5f590ce20f1bde7090ef7970686ec6738a"
        );
        assert_eq!(
            digest(&[b'a'; 64]),
            "ffe054fe7ae0cb6dc65c3af9b61d5209f439851db43d0ba5997337df154668eb"
        );
        assert_eq!(
            digest(&vec![b'a'; 1_000_000]),
            "cdc76e5c9914fb9281a1c7e284d73e67f1809a48a497200e046d39ccc7112cd0"
        );
    }

    #[test]
    fn split_updates_match_single_update_across_padding_boundaries() {
        let data = (0..=255u8).cycle().take(1000).collect::<Vec<_>>();
        for length in [55usize, 56, 63, 64, 65, 119, 120, 1000] {
            let expected = digest(&data[..length]);
            for split in [0usize, 1, 31, 64, length / 2, length] {
                let split = split.min(length);
                let mut hasher = Sha256::new();
                hasher.update(&data[..split]);
                hasher.update(&data[split..length]);
                assert_eq!(
                    hasher.finalize_hex(),
                    expected,
                    "length {length} split {split}"
                );
            }
        }
    }

    #[test]
    fn hashes_committed_fixture_like_the_manifest() {
        let path = Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../../validation/fixtures/public/synthetic-clean-side-12.mp4");
        assert_eq!(
            file_sha256_hex(&path).expect("fixture is readable"),
            "a175d350c96db3df1771c1eb141a017eaed012ae6bbd6cda739510b244113096"
        );
    }
}
