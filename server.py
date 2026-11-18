  def do_POST(self):
    if path == '/de4/api/webhooks/github':
    payload = self.read_json()
    print('GitHub webhook:', payload)
    return reply(self, 200, {'ok': True})
   path=urllib.parse.urlparse(self.path).path
   qs=urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)

   if path == '/de4/api/webhooks/github':
    try:
     n=int(self.headers.get('Content-Length','0'))
     if n < 1 or n > 30000:
      return reply(self, 400, {'error':'Invalid payload size'})

     payload=self.rfile.read(n)
     event=self.headers.get('X-GitHub-Event','unknown')
     secret=os.getenv('GITHUB_WEBHOOK_SECRET','').encode()

     if secret:
      signature=self.headers.get('X-Hub-Signature-256','')
      digest='sha256='+hmac.new(secret,payload,hashlib.sha256).hexdigest()
      if not hmac.compare_digest(signature,digest):
       return reply(self, 401, {'error':'Invalid signature'})

     print(f'GitHub webhook received: {event}')
     return reply(self, 200, {'ok':True})
    except Exception as e:
     print('GitHub webhook error:',e)
     return reply(self, 400, {'error':'Invalid payload'})
