from __future__ import annotations
import io
from aiogram import F, Router
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from ._safe import safe_edit_text
from ..services import repository as repo, excel_bridge

router=Router()

def _summary(r:dict)->str:
    labels={'material':'сырьё','component':'детали/комплектующие','product':'готовые изделия','stock_item':'складские позиции'}
    m=r.get('mapping') or {}
    lines=[
        'Excel распознан. Данные пока НЕ внесены.', '',
        f"Лист: {r.get('sheet')}",
        f"Тип: {labels.get(r.get('entity_type'),r.get('entity_type'))}",
        f"Строка заголовков: {r.get('header_row')}",
        f"Распознано записей: {r.get('total_rows')}",
        '', 'Что я понял из колонок:'
    ]
    for c in (m.get('metric_columns') or [])[:12]:
        lines.append(f"• {c.get('source_header') or 'колонка '+str(c.get('col'))} → {c.get('metric')} · {c.get('location')}")
    warnings=[str(x) for x in (r.get('warnings') or []) if str(x).strip()]
    if warnings:
        lines += ['', '⚠️ Проверьте предположения:']
        lines += [f'• {x}' for x in warnings[:8]]
    lines += ['', 'Первые данные:']
    for x in (r.get('rows') or [])[:8]:
        lines.append(f"• {x.get('source_date') or 'без даты'} · {x.get('entity_name')} · {x.get('location_name')} · {x.get('metric')} {x.get('quantity'):g}")
    lines += ['', 'Подтвердите только если распознавание верное. При отмене база не изменится.']
    return '\n'.join(lines)

def _kb(batch_id:str)->InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='✅ Всё верно — импортировать',callback_data=f'excel:confirm:{batch_id}')],
        [InlineKeyboardButton(text='❌ Отменить',callback_data=f'excel:cancel:{batch_id}')],
    ])

@router.message(F.document)
async def xlsx_document(message:Message)->None:
    doc=message.document; name=str(doc.file_name or '')
    if not name.lower().endswith(('.xlsx','.xlsm')):return
    uid=message.from_user.id if message.from_user else 0
    scope=repo.resolve_scope_chat_id(message.chat.id)
    if not repo.is_tenant_admin(scope,uid):
        await message.answer('Импорт Excel доступен владельцу или администратору этой организации.')
        return
    try:
        file=await message.bot.get_file(doc.file_id);buf=io.BytesIO();await message.bot.download_file(file.file_path,destination=buf)
        result=excel_bridge.analyze_bytes(scope,uid,buf.getvalue(),name)
        await message.answer(_summary(result),reply_markup=_kb(result['batch_id']))
    except Exception as exc:
        await message.answer(f'Excel не импортирован: {exc}')

@router.callback_query(F.data.startswith('excel:'))
async def excel_callback(callback:CallbackQuery)->None:
    parts=(callback.data or '').split(':',2)
    if len(parts)!=3:return
    uid=callback.from_user.id;scope=repo.resolve_scope_chat_id(callback.message.chat.id);action,bid=parts[1],parts[2]
    try:
        if action=='confirm':
            result=excel_bridge.confirm_import(scope,uid,bid,create_missing=True)
            text=f"Импорт завершён.\nПрименено: {result['applied']} из {result['total']}."
            if result['skipped']:text+=f"\nПропущено: {len(result['skipped'])}. Они не меняли склад."
        else:
            excel_bridge.cancel_import(scope,uid,bid);text='Импорт отменён. Данные не изменены.'
        await safe_edit_text(callback.message,text);await callback.answer()
    except Exception as exc:
        await callback.answer(str(exc)[:180],show_alert=True)
