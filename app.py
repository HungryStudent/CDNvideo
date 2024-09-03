import aiohttp
import asyncpg
from aiohttp import web

from config_reader import config


async def init_db(app):
    async with app['db'].acquire() as conn:
        await conn.execute('''
            CREATE EXTENSION IF NOT EXISTS postgis;
            CREATE TABLE IF NOT EXISTS cities (
                id SERIAL PRIMARY KEY,
                name VARCHAR(255) NOT NULL,
                location GEOGRAPHY(POINT) NOT NULL
            );
            CREATE INDEX IF NOT EXISTS location_gist_index ON cities USING GIST (location);
        ''')


async def add_city(request):
    data = await request.json()
    city_name = data.get('name')
    if not city_name:
        return web.Response(status=400, text="'name' is required")
    async with aiohttp.ClientSession() as session:
        async with session.get(
                f'https://nominatim.openstreetmap.org/search?city={city_name}&format=json&limit=1') as response:
            if response.status != 200:
                return web.Response(status=503, text="openstreetmap api error")
            data = await response.json()
            city_data = data[0]
            async with request.app['db'].acquire() as conn:
                city = await conn.fetchrow(
                    'INSERT INTO cities (name, location) VALUES ($1, ST_SetSRID(ST_MakePoint($2, $3), 4326)) RETURNING id;',
                    city_name, float(city_data["lon"]), float(city_data["lat"]))

    return web.json_response(
        data={"id": city["id"], "name": city_name, "lon": city_data["lon"], "lat": city_data["lat"]},
        status=201
    )


async def delete_city(request):
    try:
        city_id = int(request.match_info.get('city_id', None))
    except ValueError:
        return web.Response(status=400, text="city_id must be an integer")

    async with request.app['db'].acquire() as conn:
        await conn.execute('DELETE FROM cities WHERE id=$1', city_id)

    return web.Response(status=200, text="City deleted")


async def get_cities(request):
    async with request.app['db'].acquire() as conn:
        rows = await conn.fetch(
            'SELECT id, name, ST_X(location::geometry) AS lon, ST_Y(location::geometry) AS lat FROM cities')
    cities = [{'id': row['id'], 'name': row['name'], 'lon': row['lon'], 'lat': row['lat']} for row in rows]
    return web.json_response(cities)


async def get_city(request):
    try:
        city_id = int(request.match_info.get('city_id', None))
    except ValueError:
        return web.Response(status=400, text="city_id must be an integer")

    async with request.app['db'].acquire() as conn:
        row = await conn.fetchrow(
            'SELECT id, name, ST_X(location::geometry) AS lon, ST_Y(location::geometry) AS lat FROM cities WHERE id=$1',
            city_id)
        if not row:
            return web.Response(status=404, text="City not found")
    city = {'id': row['id'], 'name': row['name'], 'lon': row['lon'], 'lat': row['lat']}
    return web.json_response(city)


async def get_nearest_cities(request):
    params = request.rel_url.query
    lat = float(params.get('lat'))
    lon = float(params.get('lon'))

    async with request.app['db'].acquire() as conn:
        rows = await conn.fetch('''
            SELECT id, name, ST_X(location::geometry) AS lon, ST_Y(location::geometry) AS lat,
            ST_Distance(location, ST_SetSRID(ST_MakePoint($1, $2), 4326)) AS distance
            FROM cities
            ORDER BY location <-> ST_SetSRID(ST_MakePoint($1, $2), 4326)
            LIMIT 2;
        ''', lon, lat)

        nearest_cities = [
            {'id': row['id'], 'name': row['name'], 'lon': row['lon'], 'lat': row['lat'], "distance": row['distance']}
            for row in rows]
    return web.json_response(nearest_cities)


async def init_app():
    app = web.Application()
    app['db'] = await asyncpg.create_pool(config.DATABASE_URL)
    app.on_startup.append(init_db)

    app.router.add_post('/city', add_city)
    app.router.add_delete('/city/{city_id}', delete_city)
    app.router.add_get('/city/{city_id}', get_city)
    app.router.add_get('/city', get_cities)
    app.router.add_get('/city/nearest', get_nearest_cities)

    return app


if __name__ == '__main__':
    web.run_app(init_app())
