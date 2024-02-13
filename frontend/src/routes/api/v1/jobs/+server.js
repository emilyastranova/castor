import { CASTOR_SERVER_HOST } from '$env/static/private';
import { CASTOR_SERVER_PORT } from '$env/static/private';

/** @type {import('./$types').RequestHandler} */
export async function GET () {
  const url = new URL(`http://${CASTOR_SERVER_HOST}:${CASTOR_SERVER_PORT}/api/v1/jobs`);
  const response = await fetch(url);
  const data = await response.json();
  return new Response(JSON.stringify(data), {
    headers: {
      'content-type': 'application/json',
    },
  });
}