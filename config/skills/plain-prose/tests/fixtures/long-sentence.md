The importer reads every row from the upload and checks each value against the schema before it writes anything, and when a value fails it records the row number so the owner can fix the file later.
