import React from 'react';
import { act, fireEvent, render, waitFor } from '@testing-library/react-native';

import { MedicineImage } from '../components/MedicineImage';
import { Medicamento } from '../types/api';

it('tries API-resolved image references in order, then shows the fallback', async () => {
  const medicine = {
    id: 9,
    nome: 'Nimesulida 100mg Cimed 12 Comprimidos',
    codigo_barras: '7896523212685',
    principio_ativo: '',
    laboratorio: '',
    exige_receita: false,
    categorias: [],
    imagem_status: 'pendente',
    imagem_urls: [1, 2].map((id) => `https://images.example.test/nimesulida-${id}.jpg`),
    ofertas: [],
  } as Medicamento;

  const screen = await render(<MedicineImage medicine={medicine} />);
  const label = `Embalagem de ${medicine.nome}`;
  expect(screen.getByLabelText(label).props.source.uri).toContain('-1.jpg');
  await fireEvent(screen.getByLabelText(label), 'error');
  expect(screen.getByLabelText(label).props.source.uri).toContain('-2.jpg');
  await fireEvent(screen.getByLabelText(label), 'error');
  expect(screen.queryByLabelText(label)).toBeNull();
  expect(screen.getByText('Imagem indisponível')).toBeTruthy();
});

it('shows a neutral loading placeholder until a remote image loads', async () => {
  const medicine = {
    id: 11,
    nome: 'Produto com imagem remota',
    codigo_barras: '',
    principio_ativo: '',
    laboratorio: '',
    exige_receita: false,
    categorias: [],
    imagem_urls: ['https://images.example.test/produto.jpg'],
    ofertas: [],
  } as Medicamento;
  const screen = await render(<MedicineImage medicine={medicine} />);

  expect(screen.getByText('Carregando imagem…')).toBeTruthy();
  await fireEvent(screen.getByTestId('medicine-image'), 'load');
  expect(screen.queryByText('Carregando imagem…')).toBeNull();
});

it('labels a pharmacy generic fallback as illustrative rather than exact packaging', async () => {
  const medicine = {
    id: 12,
    nome: 'Medicamento de teste',
    codigo_barras: '7891234567895',
    principio_ativo: '',
    laboratorio: '',
    exige_receita: false,
    categorias: [],
    imagem_urls: [],
    ofertas: [{
      id: 916,
      farmacia_id: 2,
      catalogo_id: 12,
      preco: 8.5,
      quantidade_estoque: 1,
      disponivel: true,
      url_origem: 'https://www.drogariaspacheco.com.br/produto/916/p',
      imagem_generica: true,
      imagem_ref: '/catalogo/ofertas/916/imagem?v=def456',
    }],
  } as Medicamento;

  const screen = await render(<MedicineImage medicine={medicine} />);
  expect(screen.getByLabelText('Imagem ilustrativa fornecida pela farmácia para Medicamento de teste')).toBeTruthy();
});

it('falls through failed specific images to a pharmacy fallback and then the placeholder', async () => {
  const medicine = {
    id: 13,
    nome: 'Medicamento com fallback',
    codigo_barras: '7891234567895',
    principio_ativo: '',
    laboratorio: '',
    exige_receita: false,
    categorias: [],
    imagem_urls: ['https://api.example.test/catalogo/medicamentos/13/imagem'],
    ofertas: [{
      id: 917,
      farmacia_id: 2,
      catalogo_id: 13,
      preco: 8.5,
      quantidade_estoque: 1,
      disponivel: true,
      url_origem: 'https://www.drogariaspacheco.com.br/produto/917/p',
      imagem_generica: true,
      imagem_ref: '/catalogo/ofertas/917/imagem?v=generic',
    }],
  } as Medicamento;

  const screen = await render(<MedicineImage medicine={medicine} />);
  await fireEvent(screen.getByLabelText(`Embalagem de ${medicine.nome}`), 'error');
  const genericLabel = `Imagem ilustrativa fornecida pela farmácia para ${medicine.nome}`;
  expect(screen.getByLabelText(genericLabel).props.source.uri).toContain('/catalogo/ofertas/917/imagem');
  await fireEvent(screen.getByLabelText(genericLabel), 'error');
  expect(screen.queryByTestId('medicine-image')).toBeNull();
  expect(screen.getByText('Imagem ilustrativa')).toBeTruthy();
  expect(screen.getByText('Drogarias Pacheco')).toBeTruthy();
});

it.each([
  ['Farmácia Indiana', 'Farmácia Indiana'],
  ['Drogarias Pacheco', 'Drogarias Pacheco'],
  ['Drogaria Araújo', 'Drogaria Araújo'],
])('shows the Preço Bão illustration for %s when no photo is available', async (sourceName, expectedLabel) => {
  const medicine = {
    id: 20,
    nome: 'Medicamento sem foto',
    codigo_barras: '',
    principio_ativo: '',
    laboratorio: '',
    exige_receita: false,
    categorias: [],
    imagem_urls: [],
    ofertas: [{
      id: 200,
      farmacia_id: 1,
      catalogo_id: 20,
      preco: 10,
      quantidade_estoque: 1,
      disponivel: true,
      url_origem: 'https://farmacia.example.test/medicamento',
      farmacia_nome: sourceName,
    }],
  } as unknown as Medicamento;

  const screen = await render(<MedicineImage medicine={medicine} preferredOfferId={200} />);
  expect(screen.getByText('Imagem ilustrativa')).toBeTruthy();
  expect(screen.getByText(expectedLabel)).toBeTruthy();
  expect(screen.getByLabelText(new RegExp(`${expectedLabel}.*não representa a embalagem`))).toBeTruthy();
});

it('keeps the neutral placeholder when the selected offer has no identifiable supported pharmacy', async () => {
  const medicine = {
    id: 21,
    nome: 'Medicamento sem origem identificada',
    codigo_barras: '',
    principio_ativo: '',
    laboratorio: '',
    exige_receita: false,
    categorias: [],
    imagem_urls: [],
    ofertas: [{
      id: 210,
      farmacia_id: 8,
      catalogo_id: 21,
      preco: 10,
      quantidade_estoque: 1,
      disponivel: true,
      url_origem: 'https://farmacia.example.test/medicamento',
      farmacia_nome: 'Farmácia desconhecida',
    }],
  } as unknown as Medicamento;

  const screen = await render(<MedicineImage medicine={medicine} preferredOfferId={210} />);
  expect(screen.getByText('Imagem indisponível')).toBeTruthy();
  expect(screen.queryByText('Imagem ilustrativa')).toBeNull();
});

it('keeps a broken real image on the selected pharmacy fallback only', async () => {
  const medicine = {
    id: 19,
    nome: 'Produto em duas farmácias',
    codigo_barras: '',
    principio_ativo: '',
    laboratorio: '',
    exige_receita: false,
    categorias: [],
    imagem_urls: ['https://api.example.test/catalogo/medicamentos/19/imagem'],
    ofertas: [
      { id: 190, farmacia_id: 1, catalogo_id: 19, preco: 8, quantidade_estoque: 1, disponivel: true, url_origem: 'https://araujo.test/p/19', imagem_generica: true, imagem_ref: '/catalogo/ofertas/190/imagem?v=araujo' },
      { id: 191, farmacia_id: 2, catalogo_id: 19, preco: 9, quantidade_estoque: 1, disponivel: true, url_origem: 'https://pacheco.test/p/19', imagem_generica: true, imagem_ref: '/catalogo/ofertas/191/imagem?v=pacheco' },
    ],
  } as Medicamento;
  const screen = await render(<MedicineImage medicine={medicine} preferredOfferId={191} />);
  await fireEvent(screen.getByLabelText(`Embalagem de ${medicine.nome}`), 'error');
  const label = `Imagem ilustrativa fornecida pela farmácia para ${medicine.nome}`;
  expect(screen.getByLabelText(label).props.source.uri).toContain('/catalogo/ofertas/191/imagem');
  expect(screen.queryByLabelText(`Embalagem de ${medicine.nome}`)).toBeNull();
});

it('moves past a timed out image request instead of leaving a spinner indefinitely', async () => {
  jest.useFakeTimers();
  try {
    const medicine = {
      id: 14,
      nome: 'Medicamento com timeout',
      codigo_barras: '',
      principio_ativo: '',
      laboratorio: '',
      exige_receita: false,
      categorias: [],
      imagem_urls: ['https://api.example.test/catalogo/medicamentos/14/imagem'],
      ofertas: [{
        id: 918,
        farmacia_id: 1,
        catalogo_id: 14,
        preco: 8.5,
        quantidade_estoque: 1,
        disponivel: true,
        url_origem: 'https://www.araujo.com.br/produto/918/p',
        imagem_generica: true,
        imagem_ref: '/catalogo/ofertas/918/imagem?v=fallback',
      }],
    } as Medicamento;
    const screen = await render(<MedicineImage medicine={medicine} />);

    await act(async () => { jest.advanceTimersByTime(12_000); });
    expect(screen.getByLabelText(`Imagem ilustrativa fornecida pela farmácia para ${medicine.nome}`).props.source.uri)
      .toContain('/catalogo/ofertas/918/imagem');
  } finally {
    jest.useRealTimers();
  }
});

it('resets the fallback cursor when the persisted image reference changes', async () => {
  const medicine = {
    id: 10,
    nome: 'Produto teste',
    codigo_barras: '7891234567895',
    principio_ativo: '',
    laboratorio: '',
    exige_receita: false,
    categorias: [],
    imagem_status: 'pendente',
    imagem_urls: [1, 2].map((id) => `https://images.example.test/fallback-${id}.jpg`),
    ofertas: [],
  } as Medicamento;
  const view = await render(<MedicineImage medicine={medicine} />);
  const label = `Embalagem de ${medicine.nome}`;
  await fireEvent(view.getByLabelText(label), 'error');
  expect(view.getByLabelText(label).props.source.uri).toContain('fallback-2.jpg');

  await act(async () => {
    view.rerender(
      <MedicineImage
        medicine={{
          ...medicine,
          imagem_urls: ['https://cdn.example.test/updated.jpg'],
          imagem_status: 'verificada',
        }}
      />,
    );
  });
  await waitFor(() => {
    expect(view.getByLabelText(label).props.source.uri).toBe('https://cdn.example.test/updated.jpg');
  });
});
