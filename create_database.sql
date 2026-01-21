CREATE TABLE cards (
    oracle_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    edhrec_rank INTEGER
);

CREATE TABLE prices (
    oracle_id TEXT REFERENCES cards(oracle_id),
    price DECIMAL(10,2) NOT NULL,
    date TIMESTAMP NOT NULL,
    filename TEXT REFERENCES updates(filename),
    PRIMARY KEY (oracle_id, date)
);

CREATE TABLE updates (
    filename TEXT PRIMARY KEY
);