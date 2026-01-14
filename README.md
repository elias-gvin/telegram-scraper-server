#### TODO
Create a list of cli tools.
1. for search of chat ids.
2. for data dump to db -> main tool.
3. to convert from db to json.
4. to repair media.

For the tool that should dump data to db, there should be params:
- start date (if not specified - from the beginning)
- end date (if not specified - till the chat end)
- dump from the latest date in db -> if specified, system ignores everything that was already (PRESUMABLY) dumped.
We should have separate table, which stores the following data:
- latest sync. Chat that synced. Specified dates. Was media sync on/off. And was this dump successfull or not.
We would need this for figuring out quicly dates to start from next dump. 

I want separate tools:
- for authorization
- for group/channel search by name
- for dump of the specific group/channel

Also, I want to store if dump was successfull.
And error if not: "failed to download media" or "failed to finish scrape (unknown error)" or "not authorized". We should have separate table with error types.

TODO:
add search by id to search
create common cli interface for all tools
add tool for dumping everything to csv or json
fix hardcode in scrape.py, simplify interface, refactor
ensure stuff with data. You can have access to chat, but don't have permissions
mb combine every tool in 1 single pipeline (like it was with original tool)?
AAAAA
Add table which catches the last successfull data dump
Add param which downloads everything AFTER the latest successfull data dump