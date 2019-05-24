name = 'aiowetransfer'

import aiohttp
import os

try:
    import magic
    mime = magic.Magic(mime=True)
    mime_from_file = mime.from_file
except ImportError:
    import mimetypes
    def mime_from_file(filename):
        type_, _encoding = mimetypes.guess_type(filename)
        return type_ or 'application/octet-stream'
            

import logging
LOGGER = logging.getLogger('aiowetransfer')
LOGGER.addHandler(logging.NullHandler())


class AsyncWeTransfer:
    def __init__(self, x_api_key, user_identifier=None):
        self.x_api_key = x_api_key
        self.token = None
        self.user_identifier = user_identifier
        self.session = None

        # For V4 api
        self.sender = None
        self.recipients = []
        self.language = 'en'

    async def __aenter__(self):
        await self.authorize()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.close()

    async def close(self):
        if self.session:
            await self.session.close()
            self.session = None
    
    async def authorize(self):
        address = 'https://dev.wetransfer.com/v2/authorize'
        headers = {
            'Content-Type': 'application/json',
            'x-api-key': self.x_api_key,
        }

        if self.user_identifier:
            data = { 'user_identifier': self.user_identifier }
        else:
            data = None

        async with aiohttp.ClientSession(raise_for_status=True) as session:
            async with session.post(address, json=data, headers=headers) as r:
                json_resp = await r.json()
                self.token = 'Bearer ' + json_resp['token']

        headers['Authorization'] = self.token
        self.session = aiohttp.ClientSession(raise_for_status=True, headers=headers)
    
    def is_authentified(self):
        return bool(self.token)
    
    # presigned amazon s3 handler (common to board and transfer API)
    async def file_upload(self, url, file_name, mime_type, bytes_stream):
        headers = {
            'Content-Type': mime_type,
            'File': file_name,
        }

        async with aiohttp.ClientSession(raise_for_status=True) as session:
            async with session.put(url, headers=headers, data=bytes_stream) as r:
                return await r.text()
    
    ##################################################################################################################################
    # Board API https://wetransfer.github.io/wt-api-docs/index.html#board-api
    ##################################################################################################################################
    async def get_board(self, board_id):
        address = 'https://dev.wetransfer.com/v2/boards/{}'.format(board_id)
        async with self.session.get(address) as r:
            return await r.json()
    
    async def create_new_board(self, name):
        address = 'https://dev.wetransfer.com/v2/boards'
        data = { 'name': name }

        async with self.session.post(address, json=data) as r:
            json_data = await r.json()
            return json_data['id'], json_data['url']
    
    async def add_links_to_board(self, board_id, data):
        address = 'https://dev.wetransfer.com/v2/boards/{}/links'.format(board_id)
        async with self.session.post(address, json=data):
            return await self.get_board(board_id)
    
    async def add_files_to_board(self, board_id, file_paths):
        files = []
        for file_path in file_paths :
            files.append({
                'file_path': file_path,
                'file_name': os.path.basename(file_path),
                'file_size': os.path.getsize(file_path),
                'mime_type': mime_from_file(file_path)
            })
        
        address = 'https://dev.wetransfer.com/v2/boards/%s/files' % board_id

        data = []
        for f in files:
            data.append({
                'name': f['file_name'],
                'size': f['file_size']
            })

        async with self.session.post(address, json=data) as r:
            upload_plans = await r.json()
        
        for num, up in enumerate(upload_plans):
            f = files[num]
            i = 1
            
            with open(f['file_path'], 'rb') as fr:
                while True:
                    bytes_read = fr.read(up['multipart']['chunk_size'])
                    
                    if not bytes_read:
                        break

                    url = await self.request_upload_url_board(board_id, up['id'], i, up['multipart']['id'])
                    await self.file_upload(url, f['file_name'], f['mime_type'], bytes_read)
                    i += 1

            await self.complete_file_upload_board(board_id, up['id'])

        return await self.get_board(board_id)
    
    async def request_upload_url_board(self, board_id, file_id, part_number, multipart_upload_id):
        address = 'https://dev.wetransfer.com/v2/boards/{}/files/{}/upload-url/{}/{}'.format(board_id, file_id, part_number, multipart_upload_id)
        async with self.session.get(address) as r:
            return (await r.json())['url']
    
    async def complete_file_upload_board(self, board_id, file_id):
        address = 'https://dev.wetransfer.com/v2/boards/{}/files/{}/upload-complete'.format(board_id, file_id)
        return await self.session.put(address)
    
    
    ##################################################################################################################################
    # Transfer API https://wetransfer.github.io/wt-api-docs/index.html#transfer-api
    ##################################################################################################################################
    async def upload_file(self, file_path, message):
        return await self.upload_files([file_path], message)
    
    async def upload_files(self, file_paths, message):
        # multiple uploads
        files = []
        for file_path in file_paths :
            files.append({
                'file_path': file_path,
                'file_name': os.path.basename(file_path),
                'file_size': os.path.getsize(file_path),
                'mime_type': mime_from_file(file_path),
            })
        
        if not self.sender or not self.recipients:
            transfer_id, files = await self.create_new_transfer(message, files)
            
            for f in files:
                i = 1

                with open(f['file_path'], 'rb') as fr:
                    while True:
                        bytes_read = fr.read(f['chunk_size'])

                        if not bytes_read:
                            break

                        url = await self.request_upload_url(transfer_id, f['file_id'], i)
                        await self.file_upload(url, f['file_name'], f['mime_type'], bytes_read)
                        i += 1

                await self.complete_file_upload(transfer_id, f['file_id'], f['part_numbers'])

            return await self.finalize_transfer(transfer_id)
    
        else: 
            # take care, this uses wetransfer API V4, which is currently undocumented
            transfer_id, files = await self.create_new_transfer_mail(message, files, self.sender, self.recipients, self.language)
            part_numbers = 0

            for f in files:
                file_id, _chunk_size = await self.request_transfer_mail(transfer_id, f['file_name'], f['file_size'])
                
                i = 1

                with open(f['file_path'], 'rb') as fr:
                    while True:
                        bytes_read = fr.read(f['chunk_size'])

                        if not bytes_read:
                            break

                        # let's use an hard coded crc, since wetransfer doesn't use this value afterward...
                        chunk_crc = 888888888
                        
                        url = await self.request_upload_url_mail(transfer_id, file_id, i, f['chunk_size'] , chunk_crc)
                        await self.file_upload(url, f['file_name'], f['mime_type'], bytes_read)
                        i += 1
                
                await self.complete_file_upload_mail(transfer_id, f['file_id'], f['part_numbers'])
                part_numbers += f['part_numbers']

            return await self.finalize_transfer_mail(transfer_id, part_numbers)
    
    async def create_new_transfer(self, message, files):
        address = 'https://dev.wetransfer.com/v2/transfers'
        files_stream = []

        for f in files:
            files_stream.append({
                'name': f['file_name'],
                'size': f['file_size']
            })

        data = {
            'message': message,
            'files': files_stream
        }

        async with self.session.post(address, json=data) as r:
            json_resp = await r.json()
            chunk_size = json_resp['files'][0]['multipart']['chunk_size']

        for i, f in enumerate(json_resp['files']):
            files[i].update({
                'file_id': f['id'],
                'part_numbers': json_resp['files'][0]['multipart']['part_numbers'],
                'chunk_size': chunk_size,
            })

        return json_resp['id'], files
    
    async def request_upload_url(self, transfer_id, file_id, part_number):
        address = 'https://dev.wetransfer.com/v2/transfers/{}/files/{}/upload-url/{}'.format(transfer_id, file_id, part_number)
        headers = {
            'Authorization': self.token,
            'Content-Type': 'application/json',
            'x-api-key': self.x_api_key,
        }

        async with self.session.get(address, headers=headers) as r:
            return (await r.json())['url']
    
    async def complete_file_upload(self, transfer_id, file_id, part_numbers):
        address = 'https://dev.wetransfer.com/v2/transfers/{}/files/{}/upload-complete'.format(transfer_id, file_id)
        data = { 'part_numbers': part_numbers }
        
        async with self.session.put(address, json=data) as r:
            return await r.json()
    
    async def finalize_transfer(self, transfer_id):
        address = 'https://dev.wetransfer.com/v2/transfers/{}/finalize'.format(transfer_id)
        async with self.session.put(address) as r:
            return (await r.json())['url']
    
    
    ##################################################################################################################################
    # Warning, 
    # Bellow this, we use Transfer API V4,
    # Which is currently undocumented, against WeTransfer's CLUF and probably subject to changes...
    ##################################################################################################################################
    def emails(self, sender, recipients, language='en') :
        LOGGER.warning("This functionnality use automatically the WeTransfer private API V4. This is against the current WeTransfer's CLUF License. Use it only for testing purpose !")
        # initialization function
        self.sender = sender
        self.recipients = recipients
        self.language = language
    
    async def create_new_transfer_mail(self, message, files, sender, recipients, language):
        address = 'https://wetransfer.com/api/v4/transfers/email'

        files_stream = []
        for f in files:
            files_stream.append({
                'name': f['file_name'],
                'size': f['file_size'],
            })

        data = {
            'recipients': recipients,
            'message': message,
            'from': sender,
            'ui_naguage': language,
            'domain_user_id': self.user_identifier,
            'files': files_stream
        }

        async with self.session.post(address, json=data) as r:
            json_resp = await r.json()
            chunk_size = json_resp['files'][0]['chunk_size']
        
        for i, f in enumerate(json_resp['files']):
            files[i].update({
                'file_id': f['id'],
                'part_numbers': ( files[i]['file_size'] // chunk_size ) + 1,
                'chunk_size': chunk_size,
            })

        return json_resp['id'], files
     
    async def request_transfer_mail(self, transfer_id, file_name, file_size):
        address = 'https://wetransfer.com/api/v4/transfers/{}/files'.format(transfer_id)
        data = {
            'name': file_name,
            'size': file_size
        }

        async with self.session.post(address, json=data) as r:
            json_resp = await r.json()
            return json_resp['id'], json_resp['chunk_size']
    
    async def request_upload_url_mail(self, transfer_id, file_id, part_number, chunk_size, chunk_crc):
        address = 'https://wetransfer.com/api/v4/transfers/{}/files/{}/part-put-url'.format(transfer_id, file_id)
        data = {
            'chunk_number': part_number,
            'chunk_size': chunk_size,
            'chunk_crc': chunk_crc,
            'retries': 0
        }

        async with self.session.post(address, json=data) as r:
            return (await r.json())['url']
    
    async def complete_file_upload_mail(self, transfer_id, file_id, part_numbers):
        address = 'https://wetransfer.com/api/v4/transfers/{}/files/{}/finalize-mpp'.format(transfer_id, file_id)
        data = { 'chunk_count': part_numbers }

        async with self.session.put(address, json=data) as r:
            return (await r.json())['id']
    
    async def finalize_transfer_mail(self, transfer_id, part_numbers):
        address = 'https://wetransfer.com/api/v4/transfers/{}/finalize'.format(transfer_id)
        data = { 'chunk_count': part_numbers }
        async with self.session.put(address, json=data) as r:
            return (await r.json())['shortened_url']


async def __main__(api_key, file_path, message):
    async with AsyncWeTransfer(api_key) as wt:
        url = await wt.upload_file(file_path, message)
        print(url)

if __name__ == '__main__':
    import asyncio
    import sys

    api_key = input('WeTransfer API Key: ')
    message = input('Message: ')
    file_path = sys.argv[1]

    asyncio.run(__main__(api_key, file_path, message))
