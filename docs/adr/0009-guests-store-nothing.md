# Guests store nothing; only Linked usernames are persisted

Guests can run account analysis and get recommendations, and nothing about their run is persisted beyond a short-lived cache of the fetched list. The only list identity the platform stores is a Linked username attached to a logged-in account, and it is deleted when unlinked. Product events carry no personal data. This limits privacy exposure for an open-source project that handles other people's public lists.
