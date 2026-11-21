import {PDFDocument,rgb} from 'pdf-lib';
import fontkit from '@pdf-lib/fontkit';
import {readFile} from 'node:fs/promises';
import path from 'node:path';
import {db,fail,uuid} from './core';
export async function certificate(owner:string,id:string){
 const row=(await db('safar_licenses?owner=eq.'+uuid(owner)+'&track_id=eq.'+uuid(id)))[0];if(!row)fail(404,'Коммерческий сертификат доступен для треков платного тарифа.');
 const pdf=await PDFDocument.create();pdf.registerFontkit(fontkit);const font=await pdf.embedFont(await readFile(path.join(process.cwd(),'public','certificate.ttf')),{subset:true});
 pdf.setTitle('SAFAR STUDIO · Commercial License');const page=pdf.addPage([595,842]),ink=rgb(.88,.89,.91),gold=rgb(.90,.67,.28);
 page.drawRectangle({x:0,y:0,width:595,height:842,color:rgb(.045,.048,.055)});
 const write=(s:string,x:number,y:number,size=12,color=ink)=>page.drawText(s,{x,y,size,font,color});
 write('SAFAR STUDIO',44,778,19,gold);write('Коммерческая лицензия',44,687,27);write('COMMERCIAL LICENSE CERTIFICATE',44,650,11,gold);
 const lines=['Владелец',row.holder,'Track ID',row.track_id,'SHA-256 мастер-файла WAV',row.wav_sha256,'Модель и лицензия весов',row.model+'  /  MIT','Ревизия весов',row.revision,'Выдан',new Date(row.issued_at).toISOString().slice(0,10)];
 lines.forEach((s,i)=>write(String(s).slice(0,86),44,589-i*24,i%2?11:9,i%2?ink:gold));
 ['SAFAR STUDIO предоставляет владельцу неисключительное право','коммерческого использования указанного рендера в рамках прав студии.','Права на исходные записи, тексты и голоса подтверждает владелец.','Сертификат связывает лицензию с точными байтами мастер-файла.'].forEach((s,i)=>write(s,44,235-i*19,10));
 write('SAFAR STUDIO  /  v5.0',44,55,10,gold);return new Uint8Array(await pdf.save())
}
