CREATE TABLE cards (
    oracle_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    edhrec_rank INTEGER
);

CREATE TABLE prices (
    oracle_id TEXT REFERENCES cards(oracle_id),
    price DECIMAL(10,2) NOT NULL,
    date TIMESTAMP NOT NULL,  -- Changed to TIMESTAMP to include time of day
    filename TEXT REFERENCES updates(filename),  -- Relates to the update this price was inserted in
    PRIMARY KEY (oracle_id, date)  -- Ensures no duplicate prices for the same card at the same timestamp
);

CREATE TABLE updates (
    filename TEXT PRIMARY KEY
);