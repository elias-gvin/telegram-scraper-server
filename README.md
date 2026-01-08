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