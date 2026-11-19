//! Bounded JSON validation backed by Project Z's borrowed token parser.
use alloc::{
    format,
    string::{String, ToString},
    vec,
    vec::Vec,
};
pub use zconv::json::{Kind, Value};

pub const MAX_JSON_BYTES: usize = 32 * 1024 * 1024;
pub const MAX_JSON_TOKENS: usize = 1_048_576;

/// The callback cannot retain a view into the temporary token buffer.
pub fn with_json<T>(
    input: &[u8],
    f: impl FnOnce(Value<'_, '_>) -> Result<T, String>,
) -> Result<T, String> {
    if input.len() > MAX_JSON_BYTES {
        return Err("JSON input exceeds the 32 MiB limit".into());
    }
    let limits = zconv::json::Limits {
        max_input: MAX_JSON_BYTES,
        max_tokens: MAX_JSON_TOKENS,
        max_object_fields: 131_072,
        ..Default::default()
    };
    let mut capacity = (input.len() / 32).clamp(64, 4096);
    loop {
        let mut tokens = vec![zconv::json::Token::EMPTY; capacity];
        match zconv::json::parse(input, &mut tokens, limits) {
            Ok(document) => return f(document.root()),
            Err(zconv::Error::WorkspaceTooSmall { needed }) => {
                if capacity == MAX_JSON_TOKENS {
                    return Err("JSON token budget exhausted".into());
                }
                capacity = capacity.saturating_mul(2).max(needed).min(MAX_JSON_TOKENS);
            }
            Err(error) => return Err(format!("Invalid JSON: {error}")),
        }
    }
}

pub fn string(value: Value<'_, '_>) -> Option<String> {
    let s = value.as_string()?;
    if let Some(plain) = s.as_str() {
        Some(plain.to_string())
    } else {
        String::from_utf8(s.bytes().collect::<Vec<u8>>()).ok()
    }
}
pub fn get_str(value: Value<'_, '_>, key: &str) -> Option<String> {
    value.get(key).and_then(string)
}
pub fn req_str(value: Value<'_, '_>, key: &str) -> Result<String, String> {
    get_str(value, key).ok_or_else(|| format!("Missing or invalid string field: {key}"))
}
pub fn quote(value: &str) -> String {
    use core::fmt::Write;
    let mut out = String::with_capacity(value.len() + 2);
    out.push('"');
    for ch in value.chars() {
        match ch {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            '\u{08}' => out.push_str("\\b"),
            '\u{0c}' => out.push_str("\\f"),
            c if c < ' ' => {
                let _ = write!(out, "\\u{:04x}", c as u32);
            }
            c => out.push(c),
        }
    }
    out.push('"');
    out
}
pub fn raw(value: Value<'_, '_>) -> String {
    core::str::from_utf8(value.raw()).unwrap_or("").to_string()
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn decoding_and_quoting_roundtrip() {
        let s = "hello \"\\\n汉字 😀\u{01}";
        with_json(quote(s).as_bytes(), |v| {
            assert_eq!(string(v).as_deref(), Some(s));
            Ok(())
        })
        .unwrap();
    }
    #[test]
    fn duplicate_and_escaped_duplicate_keys_are_rejected() {
        for b in [br#"{"x":1,"x":2}"#.as_slice(), br#"{"x":1,"\u0078":2}"#] {
            assert!(with_json(b, |_| Ok(())).is_err());
        }
    }
    #[test]
    fn grows_index_without_changing_input() {
        let input = format!("[{}]", vec!["true"; 5000].join(","));
        let count = with_json(input.as_bytes(), |v| Ok(v.array().count())).unwrap();
        assert_eq!(count, 5000);
    }
}
