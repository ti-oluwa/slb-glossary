import asyncio

import slb_glossary as slb


async def main() -> None:
    async with (
        slb.local.database() as db,
        slb.live.session(initialize=False, headless=False, block=False) as session,
    ):
        # Local first; only opens a live page if the local DB has nothing.
        # `persist=True` writes whatever came back live into `db`.
        async for result in slb.search("clathrates", db=db, session=session, persist=True):
            print(result.value.term, "-", result.value.definition)

        # A repeat call for the same query is now served from `db` alone.
        async for result in slb.search("clathrates", db=db):
            print("(cached)", result.value.term)
        
        print(await slb.query.get_random_term(db=db))


if __name__ == "__main__":
    asyncio.run(main())
