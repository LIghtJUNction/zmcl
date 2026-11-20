//! Resolve the built-in page titles once; drawing only borrows static text.
use zi18n::{Catalog, Catalogs, Locale, Message};

static ENGLISH: [Message<'static>; 9] = [
    Message::text("page.library", "Library"),
    Message::text("page.discover", "Discover mods"),
    Message::text("page.packs", "Modpacks"),
    Message::text("page.accounts", "Accounts"),
    Message::text("page.settings", "Settings"),
    Message::text("page.worlds", "Worlds"),
    Message::text("page.backups", "Backups"),
    Message::text("page.resources", "Resources"),
    Message::text("page.servers", "Servers"),
];

pub(super) struct EnglishTitles {
    pub(super) library: &'static str,
    pub(super) discover: &'static str,
    pub(super) packs: &'static str,
    pub(super) accounts: &'static str,
    pub(super) settings: &'static str,
    pub(super) worlds: &'static str,
    pub(super) backups: &'static str,
    pub(super) resources: &'static str,
    pub(super) servers: &'static str,
}

impl EnglishTitles {
    pub(super) fn new() -> Self {
        let en = Locale::parse("en").expect("the built-in English locale is valid");
        let tables = [Catalog::new(en, &ENGLISH).expect("the built-in title catalog is valid")];
        let catalogs = Catalogs::new(&tables, &[]).expect("the built-in title catalogs are valid");
        let title = |key: &'static str| -> &'static str {
            catalogs
                .lookup(en, key)
                .expect("the built-in title exists")
                .text()
                .expect("the built-in title is plain text")
        };
        Self {
            library: title("page.library"),
            discover: title("page.discover"),
            packs: title("page.packs"),
            accounts: title("page.accounts"),
            settings: title("page.settings"),
            worlds: title("page.worlds"),
            backups: title("page.backups"),
            resources: title("page.resources"),
            servers: title("page.servers"),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::EnglishTitles;

    #[test]
    fn titles_preserve_existing_english_bytes() {
        let titles = EnglishTitles::new();
        for (actual, expected) in [
            (titles.library, "Library"),
            (titles.discover, "Discover mods"),
            (titles.packs, "Modpacks"),
            (titles.accounts, "Accounts"),
            (titles.settings, "Settings"),
            (titles.worlds, "Worlds"),
            (titles.backups, "Backups"),
            (titles.resources, "Resources"),
            (titles.servers, "Servers"),
        ] {
            assert_eq!(actual.as_bytes(), expected.as_bytes());
        }
    }
}
