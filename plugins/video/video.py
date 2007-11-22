import transcode, os, socket, re
from Cheetah.Template import Template
from plugin import Plugin
from urllib import unquote_plus, quote, unquote
from urlparse import urlparse
from xml.sax.saxutils import escape
from lrucache import LRUCache
from UserDict import DictMixin
from datetime import timedelta
import config

SCRIPTDIR = os.path.dirname(__file__)

CLASS_NAME = 'Video'

class Video(Plugin):
    
    CONTENT_TYPE = 'x-container/tivo-videos'

    def send_file(self, handler, container, name):
        
        #No longer a 'cheep' hack :p
        if handler.headers.getheader('Range') and not handler.headers.getheader('Range') == 'bytes=0-':
            handler.send_response(206)
            handler.send_header('Connection', 'close')
            handler.send_header('Content-Type', 'video/x-tivo-mpeg')
            handler.send_header('Transfer-Encoding', 'chunked')
            handler.send_header('Server', 'TiVo Server/1.4.257.475')
            handler.end_headers()
            handler.wfile.write("\x30\x0D\x0A")
            return

        tsn =  handler.headers.getheader('tsn', '')

        o = urlparse("http://fake.host" + handler.path)
        path = unquote_plus(o[2])
        handler.send_response(200)
        handler.end_headers()
        transcode.output_video(container['path'] + path[len(name)+1:], handler.wfile, tsn)
        

    def __isdir(self, full_path):
        return os.path.isdir(full_path)

    def __duration(self, full_path):
        return transcode.video_info(full_path)[4]

    def __est_size(self, full_path):
        #Size is estimated by taking audio and video bit rate adding 2%

        if transcode.tivo_compatable(full_path):  # Is TiVo compatible mpeg2
            return int(os.stat(full_path).st_size)
        else:  # Must be re-encoded
            audioBPS = strtod(config.getAudioBR())
            videoBPS = strtod(config.getVideoBR())
            bitrate =  audioBPS + videoBPS
            return int((self.__duration(full_path)/1000)*(bitrate * 1.02 / 8))
   
    def __getMetadateFromTxt(self, full_path):
        metadata = {}

        description_file = full_path + '.txt'
        if os.path.exists(description_file):
            for line in open(description_file):
                if line.strip().startswith('#'):
                    continue
                if not ':' in line:
                    continue

                key, value = line.split(':', 1)
                key = key.strip()
                value = value.strip()

                if key.startswith('v'):
                    if key in metadata:
                        metadata[key].append(value)
                    else:
                        metadata[key] = [value]
                else:
                    metadata[key] = value

        return metadata

    def __metadata(self, full_path):

        metadata = {}

        base_path, title = os.path.split(full_path)
        metadata['title'] = '.'.join(title.split('.')[:-1])
        metadata['seriesTitle'] = os.path.split(base_path)[1]

        metadata.update( self.__getMetadateFromTxt(full_path) )
        
        metadata['size'] = self.__est_size(full_path)
        metadata['duration'] = self.__duration(full_path)
        
        duration = timedelta(milliseconds = metadata['duration'])

        min = duration.seconds / 60
        sec = duration.seconds % 60
        hours = min / 60
        min = min % 60
        metadata['iso_durarion'] = 'P' + str(duration.days) + 'DT' + str(hours) + 'H' + str(min) + 'M' + str(sec) + 'S'

        return metadata

    def QueryContainer(self, handler, query):
        
        subcname = query['Container'][0]
        cname = subcname.split('/')[0]
         
        if not handler.server.containers.has_key(cname) or not self.get_local_path(handler, query):
            handler.send_response(404)
            handler.end_headers()
            return
        
        def video_file_filter(file):
            path = self.get_local_path(handler, query)
            full_path = os.path.join(path, file)
            if os.path.isdir(full_path):
                return True
            return transcode.suported_format(full_path)

        files, total, start = self.get_files(handler, query, video_file_filter)

        videos = []
        for file in files:
            path = self.get_local_path(handler, query)
            full_path = os.path.join(path, file)
            
            video = VideoDetails()
            video['name'] = file
            video['title'] = file
            video['is_dir'] = self.__isdir(full_path)
            if not  video['is_dir']:
                video['title'] = '.'.join(file.split('.')[:-1])
                video.update(self.__metadata(full_path))

            videos.append(video)

        handler.send_response(200)
        handler.end_headers()
        t = Template(file=os.path.join(SCRIPTDIR,'templates', 'container.tmpl'))
        t.name = subcname
        t.total = total
        t.start = start
        t.videos = videos
        t.quote = quote
        t.escape = escape
        handler.wfile.write(t)

    def TVBusQuery(self, handler, query):
       
        file = query['File'][0]
        path = self.get_local_path(handler, query)
        file_path = os.path.join(path, file)

        
        file_info = VideoDetails()
        file_info.update(self.__metadata(file_path))

        print file_info

        handler.send_response(200)
        handler.end_headers()
        t = Template(file=os.path.join(SCRIPTDIR,'templates', 'TvBus.tmpl'))
        t.video = file_info
        handler.wfile.write(t)
    
class VideoDetails(DictMixin):
   
    def __init__(self, d = None):
        if d:
            self.d = d
        else:
            self.d = {}

    def __getitem__(self, key):
        if key not in self.d:
            self.d[key] = self.default(key)
        return self.d[key]

    def __contains__(self, key):
        return True

    def __setitem__(self, key, value):
        self.d[key] = value

    def __delitem__(self):
        del self.d[key]
    
    def keys(self):
        return self.d.keys()
    
    def __iter__(self):
        return self.d.__iter__()

    def iteritems(self):
        return self.d.iteritems()

    def default(self, key):
        defaults = {
            'showingBits' : '0',
            'episodeNumber' : '0',
            'displayMajorNumber' : '0',
            'displayMinorNumber' : '0',
            'isEpisode' : 'true',
            'colorCode' : ('COLOR', '4'),
            'showType' : ('SERIES', '5'),
            'tvRating' : ('NR', '7'),
        }
        if key in defaults:
            return defaults[key]
        elif key.startswith('v'):
            return [key + '1', key + '2']
        else:
            return key

        
# Parse a bitrate using the SI/IEEE suffix values as if by ffmpeg
# For example, 2K==2000, 2Ki==2048, 2MB==16000000, 2MiB==16777216
# Algorithm: http://svn.mplayerhq.hu/ffmpeg/trunk/libavcodec/eval.c
def strtod(value):
    prefixes = {"y":-24,"z":-21,"a":-18,"f":-15,"p":-12,"n":-9,"u":-6,"m":-3,"c":-2,"d":-1,"h":2,"k":3,"K":3,"M":6,"G":9,"T":12,"P":15,"E":18,"Z":21,"Y":24}
    p = re.compile(r'^(\d+)(?:([yzafpnumcdhkKMGTPEZY])(i)?)?([Bb])?$')
    m = p.match(value)
    if m is None:
        raise SyntaxError('Invalid bit value syntax')
    (coef, prefix, power, byte) = m.groups()
    if prefix is None:
        value = float(coef)
    else:
        exponent = float(prefixes[prefix])
        if power == "i":
            # Use powers of 2
            value = float(coef) * pow(2.0, exponent/0.3)
        else:
            # Use powers of 10
            value = float(coef) * pow(10.0, exponent)
    if byte == "B": # B==Byte, b=bit
        value *= 8;
    return value
